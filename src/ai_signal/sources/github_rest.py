"""Real (read-only) GitHub Search API client for phase 2B1.

This module adds a controlled, read-only path to the public GitHub Search
API on top of the existing :class:`~ai_signal.sources.github.GitHubSource` /
:func:`~ai_signal.pipeline.collect.collect_source_once` machinery::

    GitHubSearchSpec -> GitHubRestClient -> GitHubSource -> SourceBatch
                                                     -> collect_source_once

Hard rules enforced here:

* only the Python standard library is used (``urllib.request`` /
  ``urllib.parse`` / ``urllib.error``);
* the only reachable host is ``api.github.com`` over HTTPS (no user
  credentials, no alternate port); redirects away from it are blocked;
* no token / cookie / authorization header is ever sent - anonymous public
  data only;
* the raw query string never reaches the cursor, the config snapshot, logs,
  warnings or exception messages - only its SHA-256 does;
* cursors are the stable form ``page:N`` (never a URL or query) and point at
  the next page to request; when ``max_pages`` is reached (or results run out)
  the cursor wraps back to ``page:1`` so a scope is re-scanned periodically;
  SQLite dedup keeps ``raw_signal`` stable across re-scans;
* every failure is surfaced as a :class:`GitHubClientError` carrying a
  stable code and nothing sensitive.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, List, Mapping, Optional, Protocol, Tuple
from urllib.parse import urlencode, urlparse

from ..domain.models import now_utc
from .github import (
    ERR_HTTP_AUTH,
    ERR_HTTP_FAILURE,
    ERR_INVALID_CONTENT_TYPE,
    ERR_INVALID_CURSOR,
    ERR_INVALID_JSON,
    ERR_INVALID_RESPONSE,
    ERR_NETWORK_FAILURE,
    ERR_NETWORK_TIMEOUT,
    ERR_RATE_LIMITED,
    ERR_REDIRECT_BLOCKED,
    ERR_RESPONSE_TOO_LARGE,
    GitHubClientError,
    GitHubPage,
)

_HOST = "api.github.com"
_BASE_URL = "https://api.github.com/search/repositories"
_DEFAULT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2 MiB
_SEARCH_CAP = 1000  # GitHub Search never returns more than 1000 results.

# Fixed request headers. No cookie, authorization or environment data.
_DEFAULT_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "ai-signal-agent",
}


def _has_control_characters(text: str) -> bool:
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text)


@dataclass(frozen=True)
class GitHubSearchSpec:
    """An immutable, validated GitHub repository-search definition.

    The raw ``query`` is used only to build the HTTPS request; everything
    persisted elsewhere uses :attr:`query_sha256`.
    """

    query: str
    sort: str = "updated"
    order: str = "desc"
    per_page: int = 10
    max_pages: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.query, str):
            raise ValueError("query must be a string")
        if not 1 <= len(self.query.strip()) <= 256:
            raise ValueError("query length out of range")
        if _has_control_characters(self.query):
            raise ValueError("query contains control characters")
        if self.sort not in ("updated", "stars"):
            raise ValueError("sort must be 'updated' or 'stars'")
        if self.order not in ("asc", "desc"):
            raise ValueError("order must be 'asc' or 'desc'")
        if isinstance(self.per_page, bool) or not isinstance(self.per_page, int):
            raise ValueError("per_page must be an integer")
        if not 1 <= self.per_page <= 25:
            raise ValueError("per_page must be between 1 and 25")
        if isinstance(self.max_pages, bool) or not isinstance(self.max_pages, int):
            raise ValueError("max_pages must be an integer")
        if not 1 <= self.max_pages <= 3:
            raise ValueError("max_pages must be between 1 and 3")

    @property
    def source_version(self) -> str:
        return "github-rest-v1"

    @property
    def query_sha256(self) -> str:
        canonical = json.dumps(
            {
                "query": self.query,
                "sort": self.sort,
                "order": self.order,
                "per_page": self.per_page,
                "max_pages": self.max_pages,
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class HttpResponse:
    """A minimal HTTP response value object used by transports."""

    status: int
    headers: Mapping[str, str]
    body: bytes
    final_url: str

    def __post_init__(self) -> None:
        if isinstance(self.status, bool) or not isinstance(self.status, int):
            raise TypeError("status must be an int")
        if not isinstance(self.body, (bytes, bytearray)):
            raise TypeError("body must be bytes")
        if not isinstance(self.final_url, str) or not self.final_url:
            raise ValueError("final_url must be a non-empty string")
        object.__setattr__(self, "body", bytes(self.body))


class HttpTransport(Protocol):
    """Structural protocol for anything that can perform a GET request."""

    def get(  # pragma: no cover - protocol body
        self,
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> HttpResponse:
        ...


def _header_get(headers: Any, name: str) -> str:
    """Case-insensitive header lookup tolerant of dict / Message inputs."""
    try:
        value = headers.get(name)
    except AttributeError:
        value = None
    if value is not None:
        return str(value)
    lowered = name.lower()
    if isinstance(headers, Mapping):
        for key, val in headers.items():
            if str(key).lower() == lowered:
                return "" if val is None else str(val)
    return ""


def _classify_transport_error(exc: BaseException) -> str:
    """Map a raw transport exception to a stable code (no sensitive data)."""
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, TimeoutError):
        return ERR_NETWORK_TIMEOUT
    text = str(reason).lower()
    if "timed out" in text or "timeout" in text:
        return ERR_NETWORK_TIMEOUT
    return ERR_NETWORK_FAILURE


class _GitHubRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Block any redirect away from https://api.github.com."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        try:
            parsed = urlparse(newurl)
        except (TypeError, ValueError):
            raise GitHubClientError(ERR_REDIRECT_BLOCKED)
        if (
            parsed.scheme != "https"
            or parsed.hostname != _HOST
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
        ):
            raise GitHubClientError(ERR_REDIRECT_BLOCKED)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class UrllibTransport:
    """Production HTTP transport backed by :mod:`urllib.request`.

    No network happens at construction or import time; the request is made
    only when :meth:`get` is called. Tests never instantiate this class.
    """

    def get(
        self,
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> HttpResponse:
        request = urllib.request.Request(url, headers=dict(headers), method="GET")
        opener = urllib.request.build_opener(_GitHubRedirectHandler)
        try:
            response = opener.open(request, timeout=timeout_seconds)
        except urllib.error.HTTPError as exc:
            # A non-2xx HTTP exchange: surface it for status-based mapping.
            body = _read_capped(exc, max_response_bytes)
            final_url = str(getattr(exc, "url", None) or url)
            return HttpResponse(
                status=int(exc.code), headers=exc.headers, body=body, final_url=final_url
            )
        try:
            body = _read_capped(response, max_response_bytes)
            final_url = str(response.geturl())
            status = int(response.getcode())
            resp_headers = response.headers
        finally:
            try:
                response.close()
            except Exception:  # pragma: no cover - best effort close
                pass
        return HttpResponse(
            status=status, headers=resp_headers, body=body, final_url=final_url
        )


def _read_capped(fileobj: Any, max_bytes: int) -> bytes:
    """Read at most ``max_bytes + 1`` bytes (bounded, never unbounded)."""
    try:
        return fileobj.read(max_bytes + 1)
    finally:
        try:
            fileobj.close()
        except Exception:  # pragma: no cover - best effort close
            pass


class GitHubRestClient:
    """A :class:`~ai_signal.sources.github.GitHubClient` over the Search API."""

    def __init__(
        self,
        spec: GitHubSearchSpec,
        transport: HttpTransport,
        *,
        clock: Optional[Callable[[], datetime]] = None,
        timeout_seconds: int = 10,
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        if not isinstance(spec, GitHubSearchSpec):
            raise TypeError("spec must be a GitHubSearchSpec")
        self._spec = spec
        self._transport = transport
        self._clock: Callable[[], datetime] = clock if clock is not None else now_utc
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int):
            raise ValueError("timeout_seconds must be an integer")
        if not 1 <= timeout_seconds <= 30:
            raise ValueError("timeout_seconds must be between 1 and 30")
        self._timeout_seconds = timeout_seconds
        if not isinstance(max_response_bytes, int) or max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        self._max_response_bytes = max_response_bytes

    @property
    def source_version(self) -> str:
        return self._spec.source_version

    @property
    def spec(self) -> GitHubSearchSpec:
        return self._spec

    def fetch(self, cursor: Optional[str]) -> GitHubPage:
        page = self._parse_cursor(cursor)
        url = self._build_url(page)
        try:
            response = self._transport.get(
                url, _DEFAULT_HEADERS, self._timeout_seconds, self._max_response_bytes
            )
        except GitHubClientError:
            raise
        except Exception as exc:  # noqa: BLE001 - classify into a stable code
            raise GitHubClientError(_classify_transport_error(exc)) from exc

        self._verify_final_url(response.final_url)
        if len(response.body) > self._max_response_bytes:
            raise GitHubClientError(ERR_RESPONSE_TOO_LARGE)
        if response.status != 200:
            raise GitHubClientError(self._map_status(response.status, response.headers))

        content_type = _header_get(response.headers, "Content-Type")
        if "json" not in content_type.lower():
            raise GitHubClientError(ERR_INVALID_CONTENT_TYPE)

        items, total_count = self._parse_success_body(response.body)
        next_cursor = self._compute_next_cursor(page, total_count, len(items))
        return GitHubPage(
            items=tuple(items),
            next_cursor=next_cursor,
            source_version=self._spec.source_version,
            fetched_at=self._clock(),
        )

    # ----- helpers ------------------------------------------------------- #

    def _parse_cursor(self, cursor: Optional[str]) -> int:
        if cursor is None:
            return 1
        if not isinstance(cursor, str) or not cursor.startswith("page:"):
            raise GitHubClientError(ERR_INVALID_CURSOR)
        num_part = cursor[len("page:"):]
        if not num_part.isdigit():
            raise GitHubClientError(ERR_INVALID_CURSOR)
        page = int(num_part)
        if page < 1 or page > self._spec.max_pages:
            raise GitHubClientError(ERR_INVALID_CURSOR)
        return page

    def _build_url(self, page: int) -> str:
        params = [
            ("q", self._spec.query),
            ("sort", self._spec.sort),
            ("order", self._spec.order),
            ("per_page", str(self._spec.per_page)),
            ("page", str(page)),
        ]
        return _BASE_URL + "?" + urlencode(params)

    @staticmethod
    def _verify_final_url(final_url: str) -> None:
        try:
            parsed = urlparse(final_url)
        except (TypeError, ValueError):
            raise GitHubClientError(ERR_REDIRECT_BLOCKED)
        if (
            parsed.scheme != "https"
            or parsed.hostname != _HOST
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
        ):
            raise GitHubClientError(ERR_REDIRECT_BLOCKED)

    @staticmethod
    def _map_status(status: int, headers: Any) -> str:
        if status == 401:
            return ERR_HTTP_AUTH
        if status == 429:
            return ERR_RATE_LIMITED
        if status == 403:
            if _header_get(headers, "X-RateLimit-Remaining").strip() == "0":
                return ERR_RATE_LIMITED
            return ERR_HTTP_AUTH
        return ERR_HTTP_FAILURE

    @staticmethod
    def _parse_success_body(body: bytes) -> Tuple[List[Any], int]:
        try:
            data = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise GitHubClientError(ERR_INVALID_JSON) from exc
        if not isinstance(data, dict):
            raise GitHubClientError(ERR_INVALID_RESPONSE)
        total_count = data.get("total_count")
        if isinstance(total_count, bool) or not isinstance(total_count, int) or total_count < 0:
            raise GitHubClientError(ERR_INVALID_RESPONSE)
        items = data.get("items")
        if not isinstance(items, list):
            raise GitHubClientError(ERR_INVALID_RESPONSE)
        return items, int(total_count)

    def _compute_next_cursor(self, page: int, total_count: int, item_count: int) -> str:
        visible_total = min(total_count, _SEARCH_CAP)
        has_more = (
            page < self._spec.max_pages
            and item_count > 0
            and page * self._spec.per_page < visible_total
        )
        # Periodic daily scan: the cursor points at the *next* page to request.
        # When the last page within max_pages is reached (or the result set is
        # exhausted) it wraps back to page 1 so the next run re-scans from the
        # top. SQLite dedup keeps raw_signal stable across re-scans.
        return "page:%d" % (page + 1) if has_more else "page:1"
