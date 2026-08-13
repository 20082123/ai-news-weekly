"""Structured JSON Lines logging with recursive redaction.

Output is one JSON object per line on stdout by default. No log file is
ever created. The redactor never mutates the mapping it is given and it
recursively scrubs:

* sensitive keys (token, secret, password, cookie, authorization,
  api_key, access_token, smtp, credential, ...);
* email addresses;
* sensitive parameters inside URL query strings;
* overlong strings (truncated and tagged).

Unknown objects are safely stringified rather than dropped.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from typing import Any, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


_REDACTED = "[REDACTED]"
_EMAIL_PLACEHOLDER = "[EMAIL]"
_TRUNCATION_SUFFIX = "...[TRUNCATED]"

# Substrings (matched case-insensitively against the key) that mark a value
# as sensitive. Kept broad on purpose; over-redaction is safe, under is not.
_SENSITIVE_KEY_FRAGMENTS = (
    "token",
    "secret",
    "password",
    "passwd",
    "cookie",
    "authorization",
    "authorisation",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "smtp",
    "credential",
    "private_key",
    "client_secret",
    "session",
    "bearer",
)

_MAX_STRING = 2000
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _is_sensitive_key(key: Any) -> bool:
    lowered = str(key).lower()
    return any(fragment in lowered for fragment in _SENSITIVE_KEY_FRAGMENTS)


def _truncate(value: str) -> str:
    if len(value) <= _MAX_STRING:
        return value
    return value[:_MAX_STRING] + _TRUNCATION_SUFFIX


def _redact_url(value: str) -> Optional[str]:
    """If ``value`` is an http(s) URL, return it with sensitive params redacted."""
    stripped = value.strip()
    if not stripped.startswith(("http://", "https://")):
        return None
    try:
        parsed = urlparse(stripped)
    except Exception:
        return None
    if not parsed.netloc:
        return None
    if not parsed.query:
        return value
    redacted_pairs = []
    for key, val in parse_qsl(parsed.query, keep_blank_values=True):
        redacted_pairs.append((key, _REDACTED if _is_sensitive_key(key) else val))
    rebuilt = urlunparse(
        parsed._replace(query=urlencode(redacted_pairs))
    )
    return rebuilt


def _redact_string(value: str) -> str:
    url_redacted = _redact_url(value)
    if url_redacted is not None:
        return _truncate(url_redacted)
    redacted = _EMAIL_RE.sub(_EMAIL_PLACEHOLDER, value)
    return _truncate(redacted)


def _safe_str(value: Any) -> str:
    try:
        text = str(value)
    except Exception:  # pragma: no cover - extremely defensive
        return "[UNREPRESENTABLE]"
    return _redact_string(text)


def redact(value: Any) -> Any:
    """Return a redacted copy of ``value`` (the original is never mutated)."""
    if isinstance(value, Mapping):
        return {
            key: (_REDACTED if _is_sensitive_key(key) else redact(val))
            for key, val in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact(item) for item in value]
    if isinstance(value, bool):
        # bool is a subclass of int; keep it as-is and before the int check.
        return value
    if value is None or isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return _redact_string(value)
    return _safe_str(value)


def to_json_line(record: Mapping[str, Any]) -> str:
    """Serialize ``record`` to a single redacted JSON line."""
    redacted = redact(record)
    return json.dumps(
        redacted, ensure_ascii=False, default=str, separators=(",", ":")
    )


def emit(record: Mapping[str, Any], stream=None) -> None:
    """Write one redacted JSON line to ``stream`` (default stdout)."""
    target = stream if stream is not None else sys.stdout
    target.write(to_json_line(record) + "\n")
    try:
        target.flush()
    except Exception:  # pragma: no cover - not all streams are flushable
        pass


class StructuredLogger:
    """Small helper that always emits the canonical field set."""

    def __init__(
        self,
        run_id: Optional[str] = None,
        stage: Optional[str] = None,
        source: Optional[str] = None,
        stream=None,
    ) -> None:
        self.run_id = run_id
        self.stage = stage
        self.source = source
        self.stream = stream

    def log(
        self,
        level: str,
        event: str,
        status: str = "ok",
        duration_ms: Optional[int] = None,
        error_code: Optional[str] = None,
        **fields: Any,
    ) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "run_id": self.run_id,
            "stage": self.stage,
            "source": self.source,
            "event": event,
            "status": status,
            "duration_ms": duration_ms,
            "error_code": error_code,
        }
        for key, val in fields.items():
            record[key] = val
        emit(record, self.stream)

    def debug(self, event: str, **fields: Any) -> None:
        self.log("DEBUG", event, **fields)

    def info(self, event: str, **fields: Any) -> None:
        self.log("INFO", event, **fields)

    def warning(self, event: str, **fields: Any) -> None:
        self.log("WARNING", event, **fields)

    def error(self, event: str, **fields: Any) -> None:
        self.log("ERROR", event, **fields)
