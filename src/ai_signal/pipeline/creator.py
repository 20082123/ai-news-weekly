"""Repositories and pipeline helpers for the creator-centric hub (0009)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from ..domain.models import (
    CreatorChoice,
    Feedback,
    ensure_aware_utc,
    now_utc,
)
from ..storage.sqlite import StorageError

from ..discovery.gap_routing import GAP_ROUTING, assess_evidence_gaps


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    return ensure_aware_utc(value).isoformat()


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _wrap(operation: str) -> StorageError:
    return StorageError("creator choice storage %s failed" % operation)


def _safe_execute(conn, sql: str, params, operation: str):
    try:
        return conn.execute(sql, params)
    except StorageError:
        raise
    except Exception as exc:  # noqa: BLE001 - surface as StorageError
        raise _wrap(operation) from exc


class CreatorChoiceRepository:
    def __init__(self, conn):
        self.conn = conn

    def insert_or_get(self, choice: CreatorChoice) -> CreatorChoice:
        _safe_execute(
            self.conn,
            "INSERT INTO creator_choice "
            "(id, week_key, subject, event_candidate_id, status, "
            " chosen_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(week_key, subject) DO NOTHING",
            (
                choice.id,
                choice.week_key,
                choice.subject,
                choice.event_candidate_id,
                choice.status,
                _iso(choice.chosen_at),
                _iso(choice.updated_at),
            ),
            "creator choice upsert",
        )
        stored = self.get(choice.id)
        if stored is None:  # pragma: no cover - defensive
            raise _wrap("creator choice upsert")
        return stored

    def get(self, choice_id: str) -> Optional[CreatorChoice]:
        row = _safe_execute(
            self.conn,
            "SELECT * FROM creator_choice WHERE id = ?",
            (choice_id,),
            "creator choice read",
        ).fetchone()
        return self._from_row(row) if row else None

    def update_status(self, choice_id: str, status: str) -> None:
        if status not in ("chosen", "researched", "drafted", "published", "parked"):
            raise ValueError("invalid status: %r" % status)
        _safe_execute(
            self.conn,
            "UPDATE creator_choice SET status = ?, updated_at = ? WHERE id = ?",
            (status, _iso(now_utc()), choice_id),
            "creator choice status update",
        )

    def list(self, week_key: Optional[str] = None) -> List[CreatorChoice]:
        sql = (
            "SELECT * FROM creator_choice"
            + (" WHERE week_key = ?" if week_key else "")
            + " ORDER BY chosen_at DESC, id"
        )
        rows = _safe_execute(
            self.conn,
            sql,
            (week_key,) if week_key else (),
            "creator choice list",
        ).fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row) -> CreatorChoice:
        return CreatorChoice(
            id=row["id"],
            week_key=row["week_key"],
            subject=row["subject"],
            status=row["status"],
            event_candidate_id=row["event_candidate_id"],
            chosen_at=_parse_dt(row["chosen_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )


@dataclass(frozen=True)
class GapReport:
    """The evidence holes of one choice, with per-gap routing."""

    choice_id: str
    subject: str
    linked_event: bool
    has_dossier: bool
    gaps: Tuple[Tuple[str, str, str], ...]  # (gap, 中文名, 怎么补)


def record_choice(
    conn,
    week_key: str,
    subject: str,
    event_candidate_id: Optional[str] = None,
    *,
    clock: Optional[Callable[[], "object"]] = None,
) -> CreatorChoice:
    """Register the creator's weekly choice (caller owns transaction)."""
    ts = clock if clock is not None else now_utc
    choice = CreatorChoice(
        week_key=week_key,
        subject=subject,
        event_candidate_id=event_candidate_id,
        chosen_at=ts(),
    )
    return CreatorChoiceRepository(conn).insert_or_get(choice)


def assess_choice_gaps(conn, choice: CreatorChoice) -> GapReport:
    """Compute the evidence gap list for one choice.

    When the choice links an event with a dossier, covered gaps come from the
    dossier's fact source kinds; otherwise every gap is open and research is
    the first step.
    """
    has_dossier = False
    fact_sources = []
    if choice.event_candidate_id:
        rows = _safe_execute(
            conn,
            "SELECT rf.source_kind, rf.source_url FROM research_fact rf "
            "JOIN research_dossier rd ON rd.id = rf.dossier_id "
            "WHERE rd.event_candidate_id = ?",
            (choice.event_candidate_id,),
            "gap fact read",
        ).fetchall()
        has_dossier = bool(rows)
        fact_sources = [(row["source_kind"], row["source_url"]) for row in rows]

    gaps = assess_evidence_gaps(fact_sources)
    return GapReport(
        choice_id=choice.id,
        subject=choice.subject,
        linked_event=choice.event_candidate_id is not None,
        has_dossier=has_dossier,
        gaps=tuple(
            (gap, GAP_ROUTING[gap][0], GAP_ROUTING[gap][2]) for gap in gaps
        ),
    )


def record_choice_feedback(
    conn,
    choice_id: str,
    decision: str,
    reason: str,
    *,
    audience: Optional[str] = None,
    angle: Optional[str] = None,
    usefulness: Optional[int] = None,
    published_url: Optional[str] = None,
    clock: Optional[Callable[[], "object"]] = None,
) -> Feedback:
    """Record the outcome feedback of a published (or parked) choice.

    Reuses the generic ``feedback`` table with ``target_type =
    creator_choice``; the vocabulary is the human feedback set
    ``adopted / parked / rejected``.
    """
    from ..storage.material_repositories import FeedbackRepository

    ts = clock if clock is not None else now_utc
    feedback = Feedback(
        target_type="creator_choice",
        target_id=choice_id,
        decision=decision,
        reason=reason,
        audience=audience,
        angle=angle,
        usefulness=usefulness,
        published_url=published_url,
        created_at=ts(),
    )
    # Feedback is an append-only observation log: every record is kept.
    stored = FeedbackRepository(conn).insert(feedback)
    return stored
