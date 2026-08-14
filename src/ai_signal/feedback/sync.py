"""Phase 2B2 feedback sync pipeline (offline, deterministic).

Scans first-level ``.md`` files in an inbox directory, parses their
frontmatter, and writes valid human feedback into SQLite as ``Feedback``
rows. Feedback ids are deterministic (target_id + canonical JSON of the six
feedback fields), so re-syncing the same file never duplicates a row.

Error boundary: per-file parse/validation errors are counted as ``invalid``
and skipped, but :class:`~ai_signal.storage.sqlite.StorageError` (database
lock, closed connection, INSERT failure, …) is propagated up so the caller
can roll back the entire transaction - a database failure must never leave a
partially-committed batch of Feedback rows.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple
from urllib.parse import urlparse

from ..domain.models import Feedback, deterministic_id, now_utc
from ..feedback.frontmatter import FrontmatterError, parse_frontmatter
from ..storage.material_repositories import FeedbackRepository
from ..storage.sqlite import StorageError

_MAX_FILE_BYTES = 256 * 1024
_REASON_MAX = 500
_TEXT_MAX = 200


class FeedbackSyncError(Exception):
    """Raised when a feedback file is invalid and must be skipped."""


@dataclass(frozen=True)
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
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return deterministic_id("feedback", target_id, canonical)


def sync_feedback(conn, inbox_dir: Path) -> SyncResult:
    """Scan ``inbox_dir`` (first-level ``.md`` only) and sync feedback.

    The caller owns the transaction. Returns a safe count summary. Raises
    :class:`StorageError` on database failure (the caller must roll back).
    """
    if not inbox_dir.is_dir():
        raise FeedbackSyncError("inbox_dir is not a directory")

    fb_repo = FeedbackRepository(conn)
    scanned = 0
    inserted = 0
    skipped = 0
    invalid = 0

    for entry in sorted(inbox_dir.iterdir()):
        if not entry.is_file():
            continue
        if entry.is_symlink():
            continue
        if entry.suffix != ".md":
            continue
        scanned += 1
        try:
            if entry.stat().st_size > _MAX_FILE_BYTES:
                invalid += 1
                continue
            text = entry.read_text(encoding="utf-8")
            fm, _body = parse_frontmatter(text)
        except (FrontmatterError, OSError, UnicodeDecodeError):
            invalid += 1
            continue

        try:
            if fm.get("target_type") != "material_pack":
                raise FeedbackSyncError("target_type must be material_pack")
            target_id = fm.get("target_id")
            if not isinstance(target_id, str) or not target_id.strip():
                raise FeedbackSyncError("target_id required")
            decision = fm.get("decision")
            if decision is None:
                skipped += 1
                continue
            if decision not in ("adopted", "parked", "rejected"):
                raise FeedbackSyncError("invalid decision")
            usefulness = fm.get("usefulness")
            if usefulness is not None:
                if not isinstance(usefulness, int) or isinstance(usefulness, bool):
                    raise FeedbackSyncError("usefulness must be int")
                if not 1 <= usefulness <= 5:
                    raise FeedbackSyncError("usefulness out of range")
            fields = {
                "decision": decision,
                "reason": _clean_text(fm.get("reason"), _REASON_MAX),
                "audience": _clean_text(fm.get("audience"), _TEXT_MAX),
                "angle": _clean_text(fm.get("angle"), _TEXT_MAX),
                "usefulness": usefulness,
                "published_url": _safe_https_url(fm.get("published_url")),
            }
            # Note: this call raises StorageError on database failure; it is
            # intentionally NOT caught here so the caller can roll back.
            if not fb_repo.target_exists("material_pack", target_id):
                raise FeedbackSyncError("unknown target_id")
            fb_id = _feedback_id(target_id, fields)
            if fb_repo.exists(fb_id):
                skipped += 1
                continue
            feedback = Feedback(
                id=fb_id,
                target_type="material_pack",
                target_id=target_id,
                decision=decision,
                created_at=now_utc(),
                reason=fields["reason"],
                audience=fields["audience"],
                angle=fields["angle"],
                usefulness=fields["usefulness"],
                published_url=fields["published_url"],
            )
            fb_repo.insert(feedback)
            inserted += 1
        except FeedbackSyncError:
            invalid += 1
            continue

    return SyncResult(scanned=scanned, inserted=inserted, skipped=skipped, invalid=invalid)
