"""Repositories for the phase 2C3 event-candidate tables (migration 0006).

* :class:`EventCandidateRepository` - source-independent candidate identity,
  idempotent per ``(signal_type, subject, change_summary)``;
* :class:`EventCandidateSourceRefRepository` - source-specific candidate
  references, idempotent per ``(event_candidate_id, source_kind, ref_id)``.

Rules (mirroring the other storage modules):

* all SQL is parameterized;
* repositories never call ``COMMIT`` - the caller owns the transaction;
* every database error is surfaced as :class:`StorageError`;
* identical input re-runs insert nothing and return the canonical row.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import List, Optional

from ..domain.models import EventCandidate, EventCandidateSourceRef, ensure_aware_utc
from .sqlite import StorageError


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    return ensure_aware_utc(value).isoformat()


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _wrap(operation: str) -> StorageError:
    return StorageError("event candidate storage %s failed" % operation)


def _safe_execute(conn, sql: str, params, operation: str):
    try:
        return conn.execute(sql, params)
    except StorageError:
        raise
    except Exception as exc:  # noqa: BLE001 - surface as StorageError
        raise _wrap(operation) from exc


class EventCandidateRepository:
    def __init__(self, conn):
        self.conn = conn

    def insert_or_get(self, candidate: EventCandidate) -> EventCandidate:
        """Insert the candidate or return the canonical existing row.

        Idempotent per ``(signal_type, subject, change_summary)``; a re-run
        with richer text for the same identity returns the stored row and
        never duplicates.
        """
        _safe_execute(
            self.conn,
            "INSERT INTO event_candidate "
            "(id, signal_type, subject, change_summary, affected_audience, "
            " work_impact_hypothesis, missing_evidence, research_priority, "
            " created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(signal_type, subject, change_summary) DO NOTHING",
            (
                candidate.id,
                candidate.signal_type,
                candidate.subject,
                candidate.change_summary,
                candidate.affected_audience,
                candidate.work_impact_hypothesis,
                json.dumps(list(candidate.missing_evidence), ensure_ascii=False),
                candidate.research_priority,
                _iso(candidate.created_at),
                _iso(candidate.updated_at),
            ),
            "event candidate upsert",
        )
        stored = self.get(candidate.id)
        if stored is None:  # pragma: no cover - defensive
            raise _wrap("event candidate upsert")
        return stored

    def get(self, candidate_id: str) -> Optional[EventCandidate]:
        row = _safe_execute(
            self.conn,
            "SELECT * FROM event_candidate WHERE id = ?",
            (candidate_id,),
            "event candidate read",
        ).fetchone()
        return self._from_row(row) if row else None

    def list(self, signal_type: Optional[str] = None) -> List[EventCandidate]:
        """All candidates ordered by priority (desc), optionally filtered."""
        if signal_type is not None and signal_type not in ("capability_change", "tool_workflow_change", "user_reality", "economics_access", "ecosystem_market_shift"):
            raise ValueError("invalid signal_type filter: %r" % signal_type)
        sql = (
            "SELECT * FROM event_candidate"
            + (" WHERE signal_type = ?" if signal_type else "")
            + " ORDER BY research_priority DESC, id"
        )
        rows = _safe_execute(
            self.conn,
            sql,
            (signal_type,) if signal_type else (),
            "event candidate list",
        ).fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row) -> EventCandidate:
        return EventCandidate(
            id=row["id"],
            signal_type=row["signal_type"],
            subject=row["subject"],
            change_summary=row["change_summary"],
            affected_audience=row["affected_audience"],
            work_impact_hypothesis=row["work_impact_hypothesis"],
            research_priority=int(row["research_priority"]),
            missing_evidence=tuple(json.loads(row["missing_evidence"])),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )


class EventCandidateSourceRefRepository:
    def __init__(self, conn):
        self.conn = conn

    def insert_or_get(self, ref: EventCandidateSourceRef) -> EventCandidateSourceRef:
        """Insert the source reference or return the existing row.

        Idempotent per ``(event_candidate_id, source_kind, ref_id)``.
        """
        _safe_execute(
            self.conn,
            "INSERT INTO event_candidate_source_ref "
            "(id, event_candidate_id, source_kind, ref_id, ref_label, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(event_candidate_id, source_kind, ref_id) DO NOTHING",
            (
                ref.id,
                ref.event_candidate_id,
                ref.source_kind,
                ref.ref_id,
                ref.ref_label,
                _iso(ref.created_at),
            ),
            "event candidate source ref upsert",
        )
        stored = self.get(ref.id)
        if stored is None:  # pragma: no cover - defensive
            raise _wrap("event candidate source ref upsert")
        return stored

    def get(self, ref_id: str) -> Optional[EventCandidateSourceRef]:
        row = _safe_execute(
            self.conn,
            "SELECT * FROM event_candidate_source_ref WHERE id = ?",
            (ref_id,),
            "event candidate source ref read",
        ).fetchone()
        return self._from_row(row) if row else None

    def list_for_candidate(self, event_candidate_id: str) -> List[EventCandidateSourceRef]:
        rows = _safe_execute(
            self.conn,
            "SELECT * FROM event_candidate_source_ref WHERE event_candidate_id = ? "
            "ORDER BY source_kind, ref_id",
            (event_candidate_id,),
            "event candidate source ref list",
        ).fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row) -> EventCandidateSourceRef:
        return EventCandidateSourceRef(
            id=row["id"],
            event_candidate_id=row["event_candidate_id"],
            source_kind=row["source_kind"],
            ref_id=row["ref_id"],
            ref_label=row["ref_label"],
            created_at=_parse_dt(row["created_at"]),
        )
