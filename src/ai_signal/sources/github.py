"""GitHub source adapter (phase 2A, offline only).

This module defines:

* :class:`GitHubPage` - a pure-stdlib dataclass returned by a client;
* :class:`GitHubClient` - a :class:`typing.Protocol` describing ``fetch``;
* :class:`GitHubSource` - a concrete :class:`~ai_signal.sources.base.Source`
  that turns a client page into a :class:`~ai_signal.domain.models.SourceBatch`.

Phase 2A never reaches the network. A real GitHub client is the job of a
later phase; here the client is always the offline
:class:`~ai_signal.sources.github_fixture.FixtureGitHubClient`.

Security posture:

* only an explicit field whitelist is copied into item payloads - unknown
  fields (including any credential-looking key) are dropped;
* item URLs must be ``https``;
* a single malformed item is skipped, the batch becomes ``partial`` and the
  warning is a stable code plus the item index - never the payload, a path,
  a URL or an exception message;
* a total client failure yields a ``failed`` batch with a stable warning
  code and no exception text.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Optional, Protocol, Tuple
from urllib.parse import urlparse

from ..domain.models import SourceBatch, SourceItem, ensure_aware_utc, now_utc
from .base import Source

# Stable warning codes (no payload, path, URL or exception data appended
# beyond a positional index where useful).
WARN_MALFORMED_ITEM = "GITHUB_MALFORMED_ITEM"
WARN_CLIENT_FAILURE = "GITHUB_CLIENT_FAILURE"

# Exact field whitelist copied into a repository item payload.
ALLOWED_FIELDS: Tuple[str, ...] = (
    "id",
    "full_name",
    "html_url",
    "description",
    "language",
    "stargazers_count",
    "forks_count",
    "topics",
    "pushed_at",
    "updated_at",
)

# Fields that must be present (and valid) for an item to be accepted.
REQUIRED_FIELDS: Tuple[str, ...] = ("id", "full_name", "html_url", "updated_at")


class _ItemRejected(Exception):
    """Internal sentinel: an item could not be safely parsed."""


@dataclass(frozen=True)
class GitHubPage:
    """One page of GitHub repository items returned by a client."""

    items: Tuple[Mapping[str, Any], ...]
    next_cursor: Optional[str]
    source_version: str
    fetched_at: datetime

    def __post_init__(self) -> None:
        if not self.source_version or not self.source_version.strip():
            raise ValueError("source_version must not be empty")
        if self.next_cursor is not None and (
            not isinstance(self.next_cursor, str) or not self.next_cursor.strip()
        ):
            raise ValueError("next_cursor must be a non-empty string or None")
        object.__setattr__(self, "items", tuple(self.items))
        object.__setattr__(self, "fetched_at", ensure_aware_utc(self.fetched_at))


class GitHubClient(Protocol):
    """Structural protocol for anything that can fetch GitHub pages."""

    source_version: str

    def fetch(self, cursor: Optional[str]) -> GitHubPage:  # pragma: no cover
        ...


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_optional_str(value: Any) -> bool:
    return value is None or isinstance(value, str)


def _is_safe_https_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlparse(value)
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and parsed.username is None
        and parsed.password is None
    )


class GitHubSource(Source):
    """Concrete source that adapts a :class:`GitHubClient` into batches."""

    name = "github"

    def __init__(
        self,
        client: GitHubClient,
        *,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._client = client
        self._clock: Callable[[], datetime] = clock if clock is not None else now_utc

    def collect(
        self,
        cursor: Optional[str],
        context: Mapping[str, Any],
    ) -> SourceBatch:
        del context  # not used in phase 2A; reserved for future wiring
        started = self._clock()
        try:
            page = self._client.fetch(cursor)
            raw_items = tuple(page.items)
            page_version = page.source_version
            next_cursor = page.next_cursor
            fetched_at = page.fetched_at
        except Exception:  # noqa: BLE001 - any client failure is total
            finished = self._clock()
            return SourceBatch(
                source=self.name,
                status="failed",
                started_at=started,
                finished_at=finished,
                source_version=self._safe_client_version(),
                warnings=(WARN_CLIENT_FAILURE,),
            )

        items = []
        warnings = []
        for index, raw in enumerate(raw_items):
            try:
                items.append(self._parse_item(raw, fetched_at))
            except _ItemRejected:
                warnings.append("%s:%d" % (WARN_MALFORMED_ITEM, index))

        status = "success" if not warnings else "partial"
        finished = self._clock()
        return SourceBatch(
            source=self.name,
            status=status,
            started_at=started,
            finished_at=finished,
            source_version=page_version,
            items=tuple(items),
            warnings=tuple(warnings),
            next_cursor=next_cursor,
        )

    def _safe_client_version(self) -> str:
        try:
            version = getattr(self._client, "source_version", "0")
            return str(version) if version else "0"
        except Exception:  # noqa: BLE001 - never leak while reporting failure
            return "0"

    @staticmethod
    def _parse_item(raw: Any, fetched_at: datetime) -> SourceItem:
        if not isinstance(raw, Mapping):
            raise _ItemRejected()

        # Required fields must be present.
        for required in REQUIRED_FIELDS:
            if required not in raw:
                raise _ItemRejected()

        raw_id = raw["id"]
        if not _is_int(raw_id) and not isinstance(raw_id, str):
            raise _ItemRejected()
        if isinstance(raw_id, str) and not raw_id.strip():
            raise _ItemRejected()
        external_id = str(raw_id)

        full_name = raw["full_name"]
        if not isinstance(full_name, str) or not full_name.strip():
            raise _ItemRejected()

        html_url = raw["html_url"]
        if not _is_safe_https_url(html_url):
            raise _ItemRejected()

        updated_at = raw["updated_at"]
        if not isinstance(updated_at, str) or not updated_at.strip():
            raise _ItemRejected()

        # Build a whitelist-only payload. Unknown keys are dropped, so a
        # credential-looking field on the raw item can never reach storage.
        payload = {
            "id": raw_id,
            "full_name": full_name,
            "html_url": html_url,
            "updated_at": updated_at,
        }
        optional_specs = (
            ("description", _is_optional_str),
            ("language", _is_optional_str),
            ("stargazers_count", _is_int),
            ("forks_count", _is_int),
            ("pushed_at", _is_optional_str),
        )
        for field_name, ok in optional_specs:
            if field_name in raw:
                value = raw[field_name]
                if not ok(value):
                    raise _ItemRejected()
                payload[field_name] = value
        if "topics" in raw:
            topics = raw["topics"]
            if not isinstance(topics, list) or not all(
                isinstance(t, str) for t in topics
            ):
                raise _ItemRejected()
            payload["topics"] = list(topics)

        return SourceItem(
            external_id=external_id,
            collected_at=fetched_at,
            payload=payload,
            url=html_url,
        )
