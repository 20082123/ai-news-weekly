"""Feedback sync pipeline (offline, deterministic, two-round DEC-018).

Scans first-level ``.md`` files in two optional directories:

* ``inbox_dir`` — legacy 2B material packs (``target_type: material_pack``);
* ``content_dir`` — 2F content briefs (``kind: content-brief``).

and writes valid human feedback into SQLite as ``Feedback`` rows. Feedback
ids are deterministic (target_id + canonical JSON of the nine feedback
fields), so re-syncing the same file never duplicates a row. Because the id
covers every filled field, a later round-2 fill (published_at/outcome/
lesson) creates a NEW row instead of mutating the round-1 row - judgement
evolution stays auditable (DEC-018).

Error boundary: per-file parse/validation errors are counted as ``invalid``
and skipped, but :class:`~ai_signal.storage.sqlite.StorageError` (database
lock, closed connection, INSERT failure, …) is propagated up so the caller
can roll back the entire transaction - a database failure must never leave a
partially-committed batch of Feedback rows.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse

from ..domain.models import (
    Feedback,
    content_brief_entity_id,
    deterministic_id,
    is_iso_date,
    now_utc,
)
from ..feedback.frontmatter import FrontmatterError, parse_frontmatter
from ..storage.material_repositories import FeedbackRepository
from ..storage.sqlite import StorageError

_MAX_FILE_BYTES = 256 * 1024
_REASON_MAX = 500
_TEXT_MAX = 200
_OUTCOME_MAX = 2000
_WEEK_KEY_RE = re.compile(r"^\d{4}-W\d{2}$")

# Humans fill the card in Chinese; the database keeps the canonical English
# vocabulary (AGENTS.md). Translation happens only here, at the boundary.
_DECISION_ALIASES = {
    "adopted": "adopted",
    "parked": "parked",
    "rejected": "rejected",
    "采用": "adopted",
    "暂存": "parked",
    "拒绝": "rejected",
}


class FeedbackSyncError(Exception):
    """Raised when a feedback file is invalid and must be skipped."""


@dataclass
class SyncResult:
    scanned: int
    inserted: int
    skipped: int
    invalid: int


def _has_control_chars(text: str) -> bool:
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text)


def _clean_text(value, max_len: int):
    if value is None:
        return None
    if not isinstance(value, str):
        raise FeedbackSyncError("expected string")
    if _has_control_chars(value):
        raise FeedbackSyncError("control character")
    text = value.strip()
    if len(text) > max_len:
        text = text[:max_len]
    return text or None


def _safe_https_url(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise FeedbackSyncError("published_url must be a string")
    try:
        parsed = urlparse(value)
    except (TypeError, ValueError):
        raise FeedbackSyncError("invalid url")
    if parsed.scheme != "https" or not parsed.netloc:
        raise FeedbackSyncError("published_url must be https")
    if parsed.username or parsed.password or parsed.port:
        raise FeedbackSyncError("published_url must not carry credentials/port")
    return value


def _safe_iso_date(value):
    if value is None:
        return None
    if not is_iso_date(value):
        raise FeedbackSyncError("published_at must be a date YYYY-MM-DD")
    return value


def _feedback_id(target_id: str, fields) -> str:
    canonical = json.dumps(
        {
            "target_id": target_id,
            "decision": fields["decision"],
            "reason": fields["reason"],
            "audience": fields["audience"],
            "angle": fields["angle"],
            "usefulness": fields["usefulness"],
            "published_url": fields["published_url"],
            "published_at": fields["published_at"],
            "outcome": fields["outcome"],
            "lesson": fields["lesson"],
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return deterministic_id("feedback", target_id, canonical)


def _common_fields(fm) -> dict:
    """Round-1 + round-2 fields shared by every target type."""
    decision = fm.get("decision")
    if decision is None:
        return None  # caller treats as "skipped": nothing to record yet
    decision = _DECISION_ALIASES.get(decision)
    if decision is None:
        raise FeedbackSyncError(
            "invalid decision (use 采用/暂存/拒绝 or adopted/parked/rejected)"
        )
    usefulness = fm.get("usefulness")
    if usefulness is not None:
        # Humans sometimes quote the number ("1" in the YAML); accept it.
        if isinstance(usefulness, str) and usefulness.strip().isdigit():
            usefulness = int(usefulness.strip())
        if not isinstance(usefulness, int) or isinstance(usefulness, bool):
            raise FeedbackSyncError("usefulness must be int")
        if not 1 <= usefulness <= 5:
            raise FeedbackSyncError("usefulness out of range")
    return {
        "decision": decision,
        "reason": _clean_text(fm.get("reason"), _REASON_MAX),
        "audience": _clean_text(fm.get("audience"), _TEXT_MAX),
        "angle": _clean_text(fm.get("angle"), _TEXT_MAX),
        "usefulness": usefulness,
        "published_url": _safe_https_url(fm.get("published_url")),
        "published_at": _safe_iso_date(fm.get("published_at")),
        "outcome": _clean_text(fm.get("outcome"), _OUTCOME_MAX),
        "lesson": _clean_text(fm.get("lesson"), _REASON_MAX),
    }


def _resolve_target(fb_repo, fm) -> Tuple[str, str]:
    """Return (target_type, target_id) validated against the database."""
    if fm.get("kind") == "content-brief":
        # DEC-018: brief identity is deterministic from its own frontmatter.
        week_key = fm.get("week_key")
        event_id = fm.get("event_candidate_id")
        if not isinstance(week_key, str) or not _WEEK_KEY_RE.match(week_key):
            raise FeedbackSyncError("content brief needs a valid week_key")
        if not isinstance(event_id, str) or not event_id.strip():
            raise FeedbackSyncError("content brief needs event_candidate_id")
        expected = content_brief_entity_id(week_key, event_id)
        if fm.get("brief_id") != expected:
            raise FeedbackSyncError("brief_id does not match week/event")
        if not fb_repo.target_exists("content_brief", event_id):
            raise FeedbackSyncError("unknown brief event")
        return "content_brief", expected

    if fm.get("target_type") != "material_pack":
        raise FeedbackSyncError("target_type must be material_pack")
    target_id = fm.get("target_id")
    if not isinstance(target_id, str) or not target_id.strip():
        raise FeedbackSyncError("target_id required")
    if not fb_repo.target_exists("material_pack", target_id):
        raise FeedbackSyncError("unknown target_id")
    return "material_pack", target_id


def _sync_file(fb_repo, fm) -> int:
    """Sync one parsed file.

    Returns 1 when inserted, 0 when skipped (no decision yet, or an
    identical row already recorded). Raises :class:`FeedbackSyncError` when
    invalid (counted by the caller).
    """
    fields = _common_fields(fm)
    if fields is None:
        return 0  # no decision yet: round 1 is optional
    target_type, target_id = _resolve_target(fb_repo, fm)
    fb_id = _feedback_id(target_id, fields)
    if fb_repo.exists(fb_id):
        return 0
    fb_repo.insert(Feedback(
        id=fb_id,
        target_type=target_type,
        target_id=target_id,
        decision=fields["decision"],
        created_at=now_utc(),
        reason=fields["reason"],
        audience=fields["audience"],
        angle=fields["angle"],
        usefulness=fields["usefulness"],
        published_url=fields["published_url"],
        published_at=fields["published_at"],
        outcome=fields["outcome"],
        lesson=fields["lesson"],
    ))
    return 1


def _scan_dir(fb_repo, directory: Path, result) -> None:
    """Scan first-level .md files; mutates the SyncResult counters."""
    for entry in sorted(directory.iterdir()):
        if not entry.is_file():
            continue
        if entry.is_symlink():
            continue
        if entry.suffix != ".md":
            continue
        result.scanned += 1
        try:
            if entry.stat().st_size > _MAX_FILE_BYTES:
                result.invalid += 1
                continue
            text = entry.read_text(encoding="utf-8")
            fm, _body = parse_frontmatter(text)
        except (FrontmatterError, OSError, UnicodeDecodeError):
            result.invalid += 1
            continue
        try:
            outcome = _sync_file(fb_repo, fm)
            if outcome:
                result.inserted += 1
            else:
                result.skipped += 1
        except FeedbackSyncError:
            result.invalid += 1
            continue
        # StorageError is NOT caught here: the caller must roll back.


def sync_feedback(conn, inbox_dir: Optional[Path] = None,
                  content_dir: Optional[Path] = None) -> SyncResult:
    """Scan legacy inbox and/or content-brief dirs and sync feedback.

    The caller owns the transaction. Returns a safe count summary. Raises
    :class:`StorageError` on database failure (the caller must roll back).
    """
    if inbox_dir is None and content_dir is None:
        raise FeedbackSyncError("inbox_dir and content_dir cannot both be absent")

    result = SyncResult(scanned=0, inserted=0, skipped=0, invalid=0)
    fb_repo = FeedbackRepository(conn)
    for directory in (inbox_dir, content_dir):
        if directory is None:
            continue
        if not directory.is_dir():
            raise FeedbackSyncError("directory is not a directory: %s" % directory)
        _scan_dir(fb_repo, directory, result)
    return result
