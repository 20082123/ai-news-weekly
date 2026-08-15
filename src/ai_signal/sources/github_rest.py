"""Real (read-only) GitHub API clients.

Two opt-in, read-only paths live here (this is the only production module
allowed to import ``urllib`` - see tests/regression/test_phase2a_boundaries):

* :class:`GitHubSearchSpec` / :class:`GitHubRestClient` (phase 2B1) - the
  public repository Search API, page-based, one query per scope;
* :class:`GitHubReposSpec` / :class:`GitHubReposClient` (phase 2C2-C) - a
  direct single-repository snapshot for the watchlist lane.

Hard rules enforced here:

* only the Python standard library is used (``urllib.request`` /
  ``urllib.parse`` / ``urllib.error``);
* the only reachable host is ``api.github.com`` over HTTPS (no user
  credentials, no alternate port); redirects away from it are blocked;
* no token / cookie / authorization header is ever sent - anonymous public
  data only;
* the raw query / full_name never reaches the cursor, the config snapshot,
  logs, warnings or exception messages - only their SHA-256 does;
* search cursors are the stable form ``page:N`` (never a URL or query);
  direct snapshots never paginate and refuse any cursor;
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


# --------------------------------------------------------------------------- #
# Phase 2C2-C: read-only direct repository snapshot (watchlist lane)
# --------------------------------------------------------------------------- #
# Same network posture as the search client: https://api.github.com only,
# no credentials, redirects blocked, final URL re-verified, and the raw
# full_name is used only to build the URL - only its SHA-256 may be
# persisted (config_snapshot["full_name_sha256"]).

_REPOS_PATH_TEMPLATE = "https://api.github.com/repos/{owner}/{repo}"


@dataclass(frozen=True)
class GitHubReposSpec:
    """An immutable, validated direct repository snapshot target.

    ``full_name`` is ``owner/repo`` and is used ONLY to build the HTTPS
    request; persisted state carries just :attr:`full_name_sha256`.
    """

    full_name: str

    def __post_init__(self) -> None:
        if not isinstance(self.full_name, str):
            raise ValueError("full_name must be a string")
        if _has_control_characters(self.full_name):
            raise ValueError("full_name contains control characters")
        parts = self.full_name.strip().split("/")
        if len(parts) != 2 or not all(parts):
            raise ValueError("full_name must be owner/repo")
        object.__setattr__(self, "full_name", "/".join(parts))

    @property
    def source_version(self) -> str:
        return "github-repos-v1"

    @property
    def full_name_sha256(self) -> str:
        canonical = json.dumps(
            {"full_name": self.full_name},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class GitHubReposClient:
    """A :class:`~ai_signal.sources.github.GitHubClient` over the repos API.

    Fetches exactly one repository snapshot (no pagination, ``next_cursor`` is
    always ``None``). The response object is handed to
    :class:`~ai_signal.sources.github.GitHubSource`, whose field whitelist
    decides what reaches storage - this client never stores anything itself.
    """

    def __init__(
        self,
        spec: GitHubReposSpec,
        transport: HttpTransport,
        *,
        clock: Optional[Callable[[], datetime]] = None,
        timeout_seconds: int = 10,
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        if not isinstance(spec, GitHubReposSpec):
            raise TypeError("spec must be a GitHubReposSpec")
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
    def spec(self) -> GitHubReposSpec:
        return self._spec

    def fetch(self, cursor: Optional[str]) -> GitHubPage:
        if cursor is not None:
            # Direct snapshots never paginate; an unexpected cursor means the
            # caller mixed up a search scope with a watchlist scope.
            raise GitHubClientError(ERR_INVALID_CURSOR)
        owner, repo = self._spec.full_name.split("/", 1)
        url = _REPOS_PATH_TEMPLATE.format(owner=owner, repo=repo)
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

        try:
            data = json.loads(response.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise GitHubClientError(ERR_INVALID_JSON) from exc
        if not isinstance(data, dict):
            raise GitHubClientError(ERR_INVALID_RESPONSE)
        return GitHubPage(
            items=(data,),
            next_cursor=None,
            source_version=self._spec.source_version,
            fetched_at=self._clock(),
        )

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


# --------------------------------------------------------------------------- #
# Phase 2D2: read-only research fetches (README + releases)
# --------------------------------------------------------------------------- #
# Same network posture as everything else in this module: api.github.com
# only, no credentials, redirects blocked, bounded responses. The returned
# TEXT IS UNTRUSTED INPUT: downstream code must treat it like any external
# content (control-character stripping, length caps, no execution).

_DEFAULT_TEXT_CAP = 6000  # characters kept from README/release bodies


def _decode_json_body(response: HttpResponse, max_bytes: int) -> Any:
    """Shared strict JSON decode for research fetches."""
    if len(response.body) > max_bytes:
        raise GitHubClientError(ERR_RESPONSE_TOO_LARGE)
    if response.status != 200:
        raise GitHubClientError(
            GitHubReposClient._map_status(response.status, response.headers)
        )
    content_type = _header_get(response.headers, "Content-Type")
    if "json" not in content_type.lower():
        raise GitHubClientError(ERR_INVALID_CONTENT_TYPE)
    try:
        data = json.loads(response.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise GitHubClientError(ERR_INVALID_JSON) from exc
    return data


def _verify_final_url_https(final_url: str) -> None:
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


class GitHubReadmeClient:
    """Fetch a repository README as decoded text (bounded, untrusted)."""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        timeout_seconds: int = 10,
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
        text_cap: int = _DEFAULT_TEXT_CAP,
    ) -> None:
        self._transport = transport
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int):
            raise ValueError("timeout_seconds must be an integer")
        if not 1 <= timeout_seconds <= 30:
            raise ValueError("timeout_seconds must be between 1 and 30")
        self._timeout_seconds = timeout_seconds
        if not isinstance(max_response_bytes, int) or max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        self._max_response_bytes = max_response_bytes
        if not isinstance(text_cap, int) or text_cap <= 0:
            raise ValueError("text_cap must be positive")
        self._text_cap = text_cap

    def fetch(self, full_name: str) -> str:
        """Return the decoded README text (capped), never the raw payload."""
        owner, repo = full_name.split("/", 1)
        url = _REPOS_PATH_TEMPLATE.format(owner=owner, repo=repo) + "/readme"
        try:
            response = self._transport.get(
                url, _DEFAULT_HEADERS, self._timeout_seconds, self._max_response_bytes
            )
        except GitHubClientError:
            raise
        except Exception as exc:  # noqa: BLE001 - classify into a stable code
            raise GitHubClientError(_classify_transport_error(exc)) from exc
        _verify_final_url_https(response.final_url)
        data = _decode_json_body(response, self._max_response_bytes)
        content = data.get("content") if isinstance(data, dict) else None
        if not isinstance(content, str):
            raise GitHubClientError(ERR_INVALID_RESPONSE)
        import base64

        try:
            raw = base64.b64decode(content).decode("utf-8", errors="replace")
        except (ValueError, TypeError) as exc:
            raise GitHubClientError(ERR_INVALID_RESPONSE) from exc
        return raw[: self._text_cap]


class GitHubReleasesClient:
    """Fetch recent releases of a repository (bounded list of summaries)."""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        timeout_seconds: int = 10,
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
        body_cap: int = _DEFAULT_TEXT_CAP,
        per_page: int = 5,
    ) -> None:
        self._transport = transport
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int):
            raise ValueError("timeout_seconds must be an integer")
        if not 1 <= timeout_seconds <= 30:
            raise ValueError("timeout_seconds must be between 1 and 30")
        self._timeout_seconds = timeout_seconds
        if not isinstance(max_response_bytes, int) or max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        self._max_response_bytes = max_response_bytes
        if not isinstance(body_cap, int) or body_cap <= 0:
            raise ValueError("body_cap must be positive")
        self._body_cap = body_cap
        if isinstance(per_page, bool) or not isinstance(per_page, int) or not 1 <= per_page <= 10:
            raise ValueError("per_page must be between 1 and 10")
        self._per_page = per_page

    def fetch(self, full_name: str):
        """Return recent releases as a list of safe dicts.

        Each entry: ``tag``, ``name``, ``body`` (capped text), ``published_at``
        (ISO), ``html_url`` (a first-party release URL, https-checked).
        """
        owner, repo = full_name.split("/", 1)
        url = (
            _REPOS_PATH_TEMPLATE.format(owner=owner, repo=repo)
            + "/releases?per_page=%d" % self._per_page
        )
        try:
            response = self._transport.get(
                url, _DEFAULT_HEADERS, self._timeout_seconds, self._max_response_bytes
            )
        except GitHubClientError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise GitHubClientError(_classify_transport_error(exc)) from exc
        _verify_final_url_https(response.final_url)
        data = _decode_json_body(response, self._max_response_bytes)
        if not isinstance(data, list):
            raise GitHubClientError(ERR_INVALID_RESPONSE)
        out = []
        for item in data:
            if not isinstance(item, dict):
                continue
            tag = item.get("tag_name")
            html_url = item.get("html_url")
            if not isinstance(tag, str) or not tag.strip():
                continue
            if not isinstance(html_url, str) or not html_url.startswith("https://"):
                continue
            body = item.get("body")
            body_text = body if isinstance(body, str) else ""
            out.append(
                {
                    "tag": tag,
                    "name": item.get("name") if isinstance(item.get("name"), str) else "",
                    "body": body_text[: self._body_cap],
                    "published_at": item.get("published_at")
                    if isinstance(item.get("published_at"), str)
                    else "",
                    "html_url": html_url,
                }
            )
        return out
