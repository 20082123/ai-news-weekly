"""Repositories for the phase 2C1 candidate-qualification tables (v2).

Three layers:

* :class:`CandidateRepository` - the stable global identity; upserts are
  monotonic (``last_seen_at`` never moves backwards and an older observation
  never overwrites a newer ``title``/``url``);
* :class:`CandidateDiscoveryRepository` - deterministic discovery provenance
  per ``(candidate, week, scope, lane, raw_signal)``;
* :class:`CandidateAssessmentRepository` - deterministic assessment revisions
  per ``(discovery, input_hash, policy_version)``.

Rules (mirroring the other storage modules):

* all SQL is parameterized;
* repositories never call ``COMMIT`` - the caller owns the transaction;
* every database error is surfaced as :class:`StorageError`;
* identical input re-runs insert nothing; a changed snapshot produces new
  discovery/assessment rows while the Candidate identity stays stable.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import List, Optional

from ..domain.models import (
    Candidate,
    CandidateAssessment,
    CandidateDiscovery,
    ensure_aware_utc,
)
from .sqlite import StorageError


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    return ensure_aware_utc(value).isoformat()


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _dumps(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _wrap(operation: str) -> StorageError:
    return StorageError("candidate storage %s failed" % operation)


def _safe_execute(conn, sql: str, params, operation: str):
    try:
        return conn.execute(sql, params)
    except StorageError:
        raise
    except Exception as exc:  # noqa: BLE001 - surface as StorageError
        raise _wrap(operation) from exc


class CandidateRepository:
    def __init__(self, conn):
        self.conn = conn

    def upsert(self, candidate: Candidate) -> Candidate:
        """Insert the Candidate or monotonically refresh its mutable fields.

        The id depends only on ``(source, canonical_key)``. On conflict:

        * ``first_seen_at`` becomes the historical minimum observation time
          (an old snapshot replayed after a new one moves it earlier);
        * ``last_seen_at`` becomes the historical maximum observation time and
          never moves backwards;
        * ``title`` / ``url`` / ``updated_at`` are refreshed only when the new
          observation is not older than the current one, so replaying an old
          snapshot never overwrites newer identity text.
        """
        cur = _safe_execute(
            self.conn,
            "INSERT INTO candidate "
            "(id, source, canonical_key, title, url, first_seen_at, "
            " last_seen_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(source, canonical_key) DO NOTHING",
            (
                candidate.id,
                candidate.source,
                candidate.canonical_key,
                candidate.title,
                candidate.url,
                _iso(candidate.first_seen_at),
                _iso(candidate.last_seen_at),
                _iso(candidate.created_at),
                _iso(candidate.updated_at),
            ),
            "candidate upsert",
        )
        if cur.rowcount == 1:
            return self.get(candidate.id)

        existing = self.get(candidate.id)
        if existing is None:  # pragma: no cover - defensive
            raise _wrap("candidate upsert")

        # Historical min / max of the observation time.
        first = existing.first_seen_at
        if candidate.first_seen_at is not None and (
            first is None or candidate.first_seen_at < first
        ):
            first = candidate.first_seen_at
        last = existing.last_seen_at
        if candidate.last_seen_at is not None and (
            last is None or candidate.last_seen_at > last
        ):
            last = candidate.last_seen_at

        # Identity text (title/url) refreshes only for a non-older snapshot.
        refresh_text = not (
            candidate.last_seen_at is None
            or existing.last_seen_at is None
            or candidate.last_seen_at < existing.last_seen_at
        )

        if refresh_text:
            _safe_execute(
                self.conn,
                "UPDATE candidate SET first_seen_at = ?, last_seen_at = ?, "
                "title = ?, url = ?, updated_at = ? WHERE id = ?",
                (
                    _iso(first),
                    _iso(last),
                    candidate.title,
                    candidate.url,
                    _iso(candidate.updated_at),
                    existing.id,
                ),
                "candidate refresh",
            )
        else:
            # Older observation: only correct first_seen_at / last_seen_at;
            # never regress title/url/updated_at.
            _safe_execute(
                self.conn,
                "UPDATE candidate SET first_seen_at = ?, last_seen_at = ? "
                "WHERE id = ?",
                (_iso(first), _iso(last), existing.id),
                "candidate refresh",
            )
        return self.get(candidate.id)

    def get(self, candidate_id: str) -> Optional[Candidate]:
        row = _safe_execute(
            self.conn,
            "SELECT * FROM candidate WHERE id = ?",
            (candidate_id,),
            "candidate read",
        ).fetchone()
        return self._from_row(row) if row else None

    def get_by_identity(self, source: str, canonical_key: str) -> Optional[Candidate]:
        row = _safe_execute(
            self.conn,
            "SELECT * FROM candidate WHERE source = ? AND canonical_key = ?",
            (source, canonical_key),
            "candidate identity read",
        ).fetchone()
        return self._from_row(row) if row else None

    @staticmethod
    def _from_row(row) -> Candidate:
        return Candidate(
            id=row["id"],
            source=row["source"],
            canonical_key=row["canonical_key"],
            title=row["title"],
            url=row["url"],
            first_seen_at=_parse_dt(row["first_seen_at"]),
            last_seen_at=_parse_dt(row["last_seen_at"]),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )


class CandidateDiscoveryRepository:
    def __init__(self, conn):
        self.conn = conn

    def insert_or_get(self, discovery: CandidateDiscovery) -> CandidateDiscovery:
        """Insert the deterministic discovery, or return the existing row.

        Idempotent per ``(candidate_id, week_key, scope_key, lane,
        raw_signal_id)``; a new snapshot in the same context forms a new
        Discovery while the Candidate identity is shared.
        """
        _safe_execute(
            self.conn,
            "INSERT INTO candidate_discovery "
            "(id, candidate_id, week_key, scope_key, lane, raw_signal_id, "
            " observed_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(candidate_id, week_key, scope_key, lane, raw_signal_id) "
            "DO NOTHING",
            (
                discovery.id,
                discovery.candidate_id,
                discovery.week_key,
                discovery.scope_key,
                discovery.lane,
                discovery.raw_signal_id,
                _iso(discovery.observed_at),
                _iso(discovery.created_at),
            ),
            "discovery upsert",
        )
        stored = self.get(discovery.id)
        if stored is None:  # pragma: no cover - defensive
            raise _wrap("discovery upsert")
        return stored

    def get(self, discovery_id: str) -> Optional[CandidateDiscovery]:
        row = _safe_execute(
            self.conn,
            "SELECT * FROM candidate_discovery WHERE id = ?",
            (discovery_id,),
            "discovery read",
        ).fetchone()
        return self._from_row(row) if row else None

    def exists(self, discovery_id: str) -> bool:
        row = _safe_execute(
            self.conn,
            "SELECT 1 FROM candidate_discovery WHERE id = ?",
            (discovery_id,),
            "discovery exists",
        ).fetchone()
        return row is not None

    @staticmethod
    def _from_row(row) -> CandidateDiscovery:
        return CandidateDiscovery(
            id=row["id"],
            candidate_id=row["candidate_id"],
            week_key=row["week_key"],
            scope_key=row["scope_key"],
            lane=row["lane"],
            raw_signal_id=row["raw_signal_id"],
            observed_at=_parse_dt(row["observed_at"]),
            created_at=_parse_dt(row["created_at"]),
        )


class CandidateAssessmentRepository:
    def __init__(self, conn):
        self.conn = conn

    def insert_or_get(self, assessment: CandidateAssessment) -> CandidateAssessment:
        """Insert the deterministic assessment, or return the existing row.

        Idempotent per ``(candidate_discovery_id, input_hash,
        policy_version)``; a changed snapshot (new ``input_hash``) yields a
        fresh revision while older revisions remain for audit.
        """
        _safe_execute(
            self.conn,
            "INSERT INTO candidate_assessment "
            "(id, candidate_discovery_id, policy_version, input_hash, decision, "
            " trigger_kind, trigger_summary, reason_codes, missing_evidence, "
            " attributes, assessed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(candidate_discovery_id, input_hash, policy_version) "
            "DO NOTHING",
            (
                assessment.id,
                assessment.candidate_discovery_id,
                assessment.policy_version,
                assessment.input_hash,
                assessment.decision,
                assessment.trigger_kind,
                assessment.trigger_summary,
                _dumps(list(assessment.reason_codes)),
                _dumps(list(assessment.missing_evidence)),
                _dumps(dict(assessment.attributes)),
                _iso(assessment.assessed_at),
            ),
            "assessment upsert",
        )
        stored = self.get(assessment.id)
        if stored is None:  # pragma: no cover - defensive
            raise _wrap("assessment upsert")
        return stored

    def get(self, assessment_id: str) -> Optional[CandidateAssessment]:
        row = _safe_execute(
            self.conn,
            "SELECT * FROM candidate_assessment WHERE id = ?",
            (assessment_id,),
            "assessment read",
        ).fetchone()
        return self._from_row(row) if row else None

    def exists(self, assessment_id: str) -> bool:
        row = _safe_execute(
            self.conn,
            "SELECT 1 FROM candidate_assessment WHERE id = ?",
            (assessment_id,),
            "assessment exists",
        ).fetchone()
        return row is not None

    def list_latest_for_discovery(
        self, discovery_id: str
    ) -> List[CandidateAssessment]:
        """All assessment revisions for a discovery, newest first."""
        rows = _safe_execute(
            self.conn,
            "SELECT * FROM candidate_assessment WHERE candidate_discovery_id = ? "
            "ORDER BY assessed_at DESC, id ASC",
            (discovery_id,),
            "assessment list",
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def list_for_context(
        self,
        week_key: str,
        scope_key: str,
        lane: str,
        decision: Optional[str] = None,
    ) -> List[CandidateAssessment]:
        """Latest assessment per discovery in a context, optionally filtered."""
        if decision is not None and decision not in ("research", "watch", "reject"):
            raise ValueError("invalid decision filter: %r" % decision)
        sql = (
            "SELECT ca.* FROM candidate_assessment ca "
            "JOIN candidate_discovery cd ON cd.id = ca.candidate_discovery_id "
            "WHERE cd.week_key = ? AND cd.scope_key = ? AND cd.lane = ? "
            "AND ca.id = ("
            "  SELECT ca2.id FROM candidate_assessment ca2 "
            "  WHERE ca2.candidate_discovery_id = ca.candidate_discovery_id "
            "  ORDER BY ca2.assessed_at DESC, ca2.id ASC LIMIT 1"
            ") "
            + ("AND ca.decision = ? " if decision else "") +
            "ORDER BY cd.candidate_id"
        )
        params: tuple = (week_key, scope_key, lane)
        if decision:
            params = params + (decision,)
        rows = _safe_execute(self.conn, sql, params, "assessment context list").fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row) -> CandidateAssessment:
        return CandidateAssessment(
            id=row["id"],
            candidate_discovery_id=row["candidate_discovery_id"],
            policy_version=row["policy_version"],
            input_hash=row["input_hash"],
            decision=row["decision"],
            trigger_kind=row["trigger_kind"],
            trigger_summary=row["trigger_summary"],
            reason_codes=tuple(json.loads(row["reason_codes"])),
            missing_evidence=tuple(json.loads(row["missing_evidence"])),
            attributes=json.loads(row["attributes"]),
            assessed_at=_parse_dt(row["assessed_at"]),
        )
