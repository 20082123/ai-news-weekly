"""Phase 2D3-B official announcement collection (deterministic, no LLM).

:func:`collect_official_announcements` walks the official source catalog,
fetches each RSS/Atom feed (read-only, bounded, https only) and records
first-party announcement candidates with a cleaned, capped excerpt. Entries
with unsafe (non-https / credentialed) links are skipped; a failed feed
degrades visibly and never blocks the others. Re-running is idempotent per
``(source_name, url)``.

Feed XML is untrusted input: parsed with stdlib ``xml.etree``, no external
entities are resolved, and every stored field is capped.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Callable, Optional

from ..discovery import official_catalog as catalog_module
from ..domain.models import (
    OfficialAnnouncementCandidate,
    now_utc,
)
from ..sources.official_http import (
    OfficialHttpError,
    OfficialRssClient,
    UrllibOfficialTransport,
    _is_clean_https,
    strip_html,
)
from ..storage.official_repositories import OfficialAnnouncementCandidateRepository

_SUMMARY_CAP = 500
_TITLE_CAP = 200
_MAX_ENTRIES_PER_SOURCE = 20


class OfficialCollectionError(ValueError):
    """Raised when official collection cannot proceed."""


@dataclass(frozen=True)
class OfficialCollectResult:
    """Safe, payload-free summary of one official collection run."""

    sources_attempted: int
    sources_failed: int
    created: int
    existing: int
    skipped_unsafe: int


def _entry_text(element: Optional[ET.Element]) -> str:
    if element is None or element.text is None:
        return ""
    return element.text.strip()


def _parse_feed(raw: bytes):
    """Parse RSS 2.0 or Atom into a list of (title, url, published, summary).

    Raises :class:`ValueError` on malformed XML. Entry links that are not
    clean https are dropped here (the caller counts them as skipped_unsafe
    only when everything else parsed).
    """
    root = ET.fromstring(raw)
    entries = []
    if root.tag.endswith("rss"):
        for item in root.iter("item"):
            title = _entry_text(item.find("title"))
            link = _entry_text(item.find("link"))
            published = (
                _entry_text(item.find("pubDate"))
                or _entry_text(item.find("{http://purl.org/dc/elements/1.1/}date"))
            )
            summary = _entry_text(item.find("description"))
            entries.append((title, link, published, summary))
    elif root.tag.endswith("feed"):  # Atom
        for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
            title = _entry_text(entry.find("{http://www.w3.org/2005/Atom}title"))
            link_el = entry.find("{http://www.w3.org/2005/Atom}link")
            link = (
                link_el.get("href", "").strip()
                if link_el is not None
                else ""
            )
            published = _entry_text(
                entry.find("{http://www.w3.org/2005/Atom}published")
            ) or _entry_text(entry.find("{http://www.w3.org/2005/Atom}updated"))
            summary_el = entry.find("{http://www.w3.org/2005/Atom}summary")
            summary = _entry_text(summary_el)
            entries.append((title, link, published, summary))
    else:
        raise ValueError("unsupported feed root element")
    return entries


def collect_official_announcements(
    conn,
    *,
    allow_network: bool = False,
    transport_factory: Optional[Callable[[], Any]] = None,
    clock: Optional[Callable[[], Any]] = None,
    timeout_seconds: int = 10,
) -> OfficialCollectResult:
    """Collect first-party announcements from every official feed (caller owns txn)."""
    if allow_network is False:
        from ..pipeline.collect import CollectionPolicyError

        raise CollectionPolicyError("network not allowed: --allow-network is required")

    ts = clock if clock is not None else now_utc
    repo = OfficialAnnouncementCandidateRepository(conn)
    sources = tuple(catalog_module.OFFICIAL_SOURCES)

    sources_attempted = 0
    sources_failed = 0
    created = 0
    existing = 0
    skipped_unsafe = 0

    for source in sources:
        sources_attempted += 1
        client = OfficialRssClient(
            transport_factory() if transport_factory is not None else UrllibOfficialTransport(),
            timeout_seconds=timeout_seconds,
        )
        try:
            raw = client.fetch(source.feed_url)
            entries = _parse_feed(raw)
        except (OfficialHttpError, ValueError, ET.ParseError):
            sources_failed += 1
            continue

        for title, link, published, summary in entries[:_MAX_ENTRIES_PER_SOURCE]:
            if not title or not link:
                continue
            if not _is_clean_https(link):
                skipped_unsafe += 1
                continue
            candidate = OfficialAnnouncementCandidate(
                source_name=source.name,
                title=title[:_TITLE_CAP],
                url=link,
                published_at=(published or "未知时间")[:64],
                summary=strip_html(summary.encode("utf-8", errors="replace"), _SUMMARY_CAP)
                if summary else "（无摘要）",
                status="new",
                created_at=ts(),
                updated_at=ts(),
            )
            if repo.get(candidate.id) is not None:
                existing += 1
            else:
                created += 1
            repo.insert_or_get(candidate)

    return OfficialCollectResult(
        sources_attempted=sources_attempted,
        sources_failed=sources_failed,
        created=created,
        existing=existing,
        skipped_unsafe=skipped_unsafe,
    )
