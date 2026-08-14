"""Offline, JSON-backed GitHub client for phase 2A tests and shadow runs.

:class:`FixtureGitHubClient` reads a fixture file once, validates its shape
strictly, and serves pages by exact cursor match. It never touches the
network.

Security posture:

* only the fixture SHA-256 (not its path) is exposed for config snapshots;
* errors are stable :class:`FixtureError` messages without path leakage;
* the fixture body is never printed or logged.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional

from .github import GitHubPage


class FixtureError(Exception):
    """Raised when a fixture is missing, malformed or wrongly typed."""


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _parse_aware_timestamp(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except (TypeError, ValueError) as exc:
        raise FixtureError("fixture fetched_at must be an aware ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FixtureError("fixture fetched_at must be an aware ISO timestamp")
    return parsed


class FixtureGitHubClient:
    """A :class:`~ai_signal.sources.github.GitHubClient` backed by a JSON file."""

    def __init__(self, fixture_path: Any) -> None:
        path = Path(fixture_path)
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise FixtureError("fixture could not be read") from exc
        # Only the hash is kept; the path is never stored on the instance.
        self._fixture_sha256 = _sha256_bytes(raw)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise FixtureError("fixture is not valid JSON") from exc
        self._validate(data)
        self._source_version = data["source_version"]
        self._pages = data["pages"]

    @property
    def source_version(self) -> str:
        return self._source_version

    @property
    def fixture_sha256(self) -> str:
        return self._fixture_sha256

    def fetch(self, cursor: Optional[str]) -> GitHubPage:
        for page in self._pages:
            if page["cursor_in"] == cursor:
                return GitHubPage(
                    items=tuple(page["items"]),
                    next_cursor=page["cursor_out"],
                    source_version=self._source_version,
                    fetched_at=_parse_aware_timestamp(page["fetched_at"]),
                )
        raise FixtureError("no fixture page for the requested cursor")

    @staticmethod
    def _validate(data: Any) -> None:
        if not isinstance(data, Mapping):
            raise FixtureError("fixture root must be an object")
        source_version = data.get("source_version")
        if not isinstance(source_version, str) or not source_version.strip():
            raise FixtureError("fixture source_version must be a non-empty string")
        pages = data.get("pages")
        if not isinstance(pages, list) or not pages:
            raise FixtureError("fixture pages must be a non-empty list")
        seen_cursors = set()
        for page in pages:
            FixtureGitHubClient._validate_page(page)
            cursor_in = page["cursor_in"]
            if cursor_in in seen_cursors:
                raise FixtureError("fixture cursor_in values must be unique")
            seen_cursors.add(cursor_in)

    @staticmethod
    def _validate_page(page: Any) -> None:
        if not isinstance(page, Mapping):
            raise FixtureError("fixture page must be an object")
        required = {"cursor_in", "cursor_out", "fetched_at", "items"}
        if not required.issubset(page.keys()):
            raise FixtureError("fixture page is missing required fields")
        cursor_in = page.get("cursor_in")
        if cursor_in is not None and (
            not isinstance(cursor_in, str) or not cursor_in.strip()
        ):
            raise FixtureError("fixture cursor_in must be a non-empty string or null")
        cursor_out = page.get("cursor_out")
        if cursor_out is not None and (
            not isinstance(cursor_out, str) or not cursor_out.strip()
        ):
            raise FixtureError("fixture cursor_out must be a non-empty string or null")
        fetched_at = page.get("fetched_at")
        if not isinstance(fetched_at, str):
            raise FixtureError("fixture fetched_at must be an aware ISO timestamp")
        _parse_aware_timestamp(fetched_at)
        items = page.get("items")
        if not isinstance(items, list):
            raise FixtureError("fixture items must be a list")
        for item in items:
            if not isinstance(item, Mapping):
                raise FixtureError("fixture item must be an object")
