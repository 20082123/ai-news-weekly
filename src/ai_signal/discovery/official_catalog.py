"""Phase 2D3-B official announcement source catalog (DRAFT, in-code).

Consumer-level AI changes surface first on official channels. This catalog
lists first-party RSS/Atom feeds the official sensor collects. Like the
GitHub policy catalog, it is immutable, versioned by editing this file, and
explicitly a DRAFT list pending first-user confirmation.

Only clean https feed URLs are accepted; entries with unsafe links are
skipped at collection time. Feed text is untrusted input and is capped and
tag-stripped before storage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..sources.official_http import _is_clean_https

_LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class OfficialSourceSpec:
    """One official first-party feed source."""

    name: str
    feed_url: str
    site_url: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or _LABEL_RE.fullmatch(self.name) is None:
            raise ValueError("invalid official source name")
        for field in ("feed_url", "site_url"):
            value = getattr(self, field)
            if not isinstance(value, str) or not _is_clean_https(value):
                raise ValueError("%s must be a clean https URL" % field)


# DRAFT first-party feed list (2026-08-15, not yet confirmed by the first
# user). Some endpoints may 404 or be removed by the vendor - a failed feed
# degrades visibly at collection time and never blocks the others.
OFFICIAL_SOURCES = (
    OfficialSourceSpec(
        name="google-ai-blog",
        feed_url="https://blog.google/technology/ai/rss/",
        site_url="https://blog.google/technology/ai/",
    ),
    OfficialSourceSpec(
        name="github-changelog",
        feed_url="https://github.blog/changelog/feed/",
        site_url="https://github.blog/changelog/",
    ),
    OfficialSourceSpec(
        name="anthropic-news",
        feed_url="https://www.anthropic.com/rss.xml",
        site_url="https://www.anthropic.com/news",
    ),
    OfficialSourceSpec(
        name="openai-news",
        feed_url="https://openai.com/news/rss.xml",
        site_url="https://openai.com/news/",
    ),
)
