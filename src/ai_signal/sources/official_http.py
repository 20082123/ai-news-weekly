"""Phase 2D3 read-only official page fetch (first-party evidence).

This is the SECOND production module allowed to import ``urllib`` (the other
is ``sources/github_rest.py``; the boundary regression test allows exactly
these two). Safety envelope:

* https only, credentials in the URL rejected;
* bounded response (default 512 KiB) and bounded returned text (default
  4000 characters);
* only ``text/html`` / ``text/plain`` responses are accepted;
* redirects are followed, but every hop and the final URL must stay https
  without embedded credentials (no downgrade attacks);
* the returned text is tag-stripped and control-character-stripped and is
  treated as UNTRUSTED external content by every consumer.

The transport is injectable for tests (same ``get`` shape as the GitHub
clients); tests never instantiate the production urllib transport.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping, Optional, Protocol
from urllib.parse import urlparse

_DEFAULT_HEADERS = {"User-Agent": "ai-signal-agent/1.0"}
_DEFAULT_MAX_RESPONSE_BYTES = 512 * 1024
_DEFAULT_TEXT_CAP = 4000

_TAG_RE = re.compile(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<[^>]+>")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class OfficialHttpError(Exception):
    """A stable, payload-free official-fetch failure code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


ERR_UNSAFE_URL = "OFFICIAL_UNSAFE_URL"
ERR_NETWORK_FAILURE = "OFFICIAL_NETWORK_FAILURE"
ERR_TIMEOUT = "OFFICIAL_TIMEOUT"
ERR_TOO_LARGE = "OFFICIAL_TOO_LARGE"
ERR_INVALID_CONTENT = "OFFICIAL_INVALID_CONTENT_TYPE"


class OfficialHttpTransport(Protocol):
    def get(  # pragma: no cover - protocol body
        self,
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> Any:
        ...


def _is_clean_https(url: str) -> bool:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )


def _strip_to_text(body: bytes, cap: int) -> str:
    text = body.decode("utf-8", errors="replace")
    text = _TAG_RE.sub(" ", text)
    text = _CTRL_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()[:cap]


class _HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Allow redirects only to clean https targets (no downgrade)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        if not _is_clean_https(newurl):
            raise OfficialHttpError(ERR_UNSAFE_URL)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class UrllibOfficialTransport:
    """Production transport over :mod:`urllib.request` (https only)."""

    def get(self, url, headers, timeout_seconds, max_response_bytes):
        request = urllib.request.Request(url, headers=dict(headers), method="GET")
        opener = urllib.request.build_opener(_HttpsOnlyRedirectHandler)
        try:
            response = opener.open(request, timeout=timeout_seconds)
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
                raise OfficialHttpError(ERR_TIMEOUT) from exc
            raise OfficialHttpError(ERR_NETWORK_FAILURE) from exc
        try:
            body = response.read(max_response_bytes + 1)
            final_url = str(response.geturl())
            status = int(response.getcode())
            content_type = response.headers.get("Content-Type", "") or ""
        finally:
            try:
                response.close()
            except Exception:  # pragma: no cover - best effort close
                pass
        from .github_rest import HttpResponse

        return HttpResponse(
            status=status,
            headers={"Content-Type": content_type},
            body=body,
            final_url=final_url,
        )


class OfficialHttpClient:
    """Fetch one official first-party page as bounded, stripped text."""

    def __init__(
        self,
        transport: OfficialHttpTransport,
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

    def fetch(self, url: str) -> str:
        if not _is_clean_https(url):
            raise OfficialHttpError(ERR_UNSAFE_URL)
        response = self._transport.get(
            url, _DEFAULT_HEADERS, self._timeout_seconds, self._max_response_bytes
        )
        if not _is_clean_https(response.final_url):
            raise OfficialHttpError(ERR_UNSAFE_URL)
        if len(response.body) > self._max_response_bytes:
            raise OfficialHttpError(ERR_TOO_LARGE)
        content_type = ""
        for key, value in (response.headers or {}).items():
            if str(key).lower() == "content-type":
                content_type = str(value or "")
                break
        lowered = content_type.lower()
        if "text/html" not in lowered and "text/plain" not in lowered:
            raise OfficialHttpError(ERR_INVALID_CONTENT)
        return _strip_to_text(bytes(response.body), self._text_cap)
