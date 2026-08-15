"""Repositories for the phase 2D/2E research tables (migration 0007).

* :class:`ResearchDossierRepository` - dossier revisions per event candidate;
* :class:`ResearchFactRepository` - auditable facts bound to a dossier;
* :class:`EditorialDecisionRepository` - deterministic editorial revisions.

Rules (mirroring the other storage modules): parameterized SQL, no COMMIT
(the caller owns the transaction), errors surfaced as :class:`StorageError`,
idempotent inserts that return the canonical stored row.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import List, Optional

from ..domain.models import (
    EditorialDecision,
    ResearchDossier,
    ResearchFact,
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


def _wrap(operation: str) -> StorageError:
    return StorageError("research storage %s failed" % operation)


def _safe_execute(conn, sql: str, params, operation: str):
    try:
        return conn.execute(sql, params)
    except StorageError:
        raise
    except Exception as exc:  # noqa: BLE001 - surface as StorageError
        raise _wrap(operation) from exc


class ResearchDossierRepository:
    def __init__(self, conn):
        self.conn = conn

    def insert_or_get(self, dossier: ResearchDossier) -> ResearchDossier:
        _safe_execute(
            self.conn,
            "INSERT INTO research_dossier "
            "(id, event_candidate_id, status, bundle_hash, summary_judgment, "
            " timeline, target_audience, job_to_be_done, limits_unknowns, "
            " forbidden_claims, needs_testing, test_plan, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO NOTHING",
            (
                dossier.id,
                dossier.event_candidate_id,
                dossier.status,
                dossier.bundle_hash,
                dossier.summary_judgment,
                json.dumps(list(dossier.timeline), ensure_ascii=False),
                dossier.target_audience,
                dossier.job_to_be_done,
                json.dumps(list(dossier.limits_unknowns), ensure_ascii=False),
                json.dumps(list(dossier.forbidden_claims), ensure_ascii=False),
                1 if dossier.needs_testing else 0,
                json.dumps(list(dossier.test_plan), ensure_ascii=False),
                _iso(dossier.created_at),
                _iso(dossier.updated_at),
            ),
            "dossier upsert",
        )
        stored = self.get(dossier.id)
        if stored is None:  # pragma: no cover - defensive
            raise _wrap("dossier upsert")
        return stored

    def get(self, dossier_id: str) -> Optional[ResearchDossier]:
        row = _safe_execute(
            self.conn,
            "SELECT * FROM research_dossier WHERE id = ?",
            (dossier_id,),
            "dossier read",
        ).fetchone()
        return self._from_row(row) if row else None

    def list_for_event(self, event_candidate_id: str) -> List[ResearchDossier]:
        rows = _safe_execute(
            self.conn,
            "SELECT * FROM research_dossier WHERE event_candidate_id = ? "
            "ORDER BY created_at DESC, id",
            (event_candidate_id,),
            "dossier list",
        ).fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row) -> ResearchDossier:
        return ResearchDossier(
            id=row["id"],
            event_candidate_id=row["event_candidate_id"],
            status=row["status"],
            bundle_hash=row["bundle_hash"],
            summary_judgment=row["summary_judgment"],
            timeline=tuple(json.loads(row["timeline"])),
            target_audience=row["target_audience"],
            job_to_be_done=row["job_to_be_done"],
            limits_unknowns=tuple(json.loads(row["limits_unknowns"])),
            forbidden_claims=tuple(json.loads(row["forbidden_claims"])),
            needs_testing=bool(row["needs_testing"]),
            test_plan=tuple(json.loads(row["test_plan"])),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )


class ResearchFactRepository:
    def __init__(self, conn):
        self.conn = conn

    def insert_or_get(self, fact: ResearchFact) -> ResearchFact:
        _safe_execute(
            self.conn,
            "INSERT INTO research_fact "
            "(id, dossier_id, kind, text, source_kind, source_url, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(dossier_id, kind, source_url, text) DO NOTHING",
            (
                fact.id,
                fact.dossier_id,
                fact.kind,
                fact.text,
                fact.source_kind,
                fact.source_url,
                _iso(fact.created_at),
            ),
            "fact upsert",
        )
        stored = self.get(fact.id)
        if stored is None:  # pragma: no cover - defensive
            raise _wrap("fact upsert")
        return stored

    def get(self, fact_id: str) -> Optional[ResearchFact]:
        row = _safe_execute(
            self.conn,
            "SELECT * FROM research_fact WHERE id = ?",
            (fact_id,),
            "fact read",
        ).fetchone()
        return self._from_row(row) if row else None

    def list_for_dossier(self, dossier_id: str) -> List[ResearchFact]:
        rows = _safe_execute(
            self.conn,
            "SELECT * FROM research_fact WHERE dossier_id = ? "
            "ORDER BY kind, id",
            (dossier_id,),
            "fact list",
        ).fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row) -> ResearchFact:
        return ResearchFact(
            id=row["id"],
            dossier_id=row["dossier_id"],
            kind=row["kind"],
            text=row["text"],
            source_kind=row["source_kind"],
            source_url=row["source_url"],
            created_at=_parse_dt(row["created_at"]),
        )


class EditorialDecisionRepository:
    def __init__(self, conn):
        self.conn = conn

    def insert_or_get(self, decision: EditorialDecision) -> EditorialDecision:
        _safe_execute(
            self.conn,
            "INSERT INTO editorial_decision "
            "(id, dossier_id, policy_version, input_hash, decision, "
            " reason_codes, decided_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(dossier_id, policy_version, input_hash) DO NOTHING",
            (
                decision.id,
                decision.dossier_id,
                decision.policy_version,
                decision.input_hash,
                decision.decision,
                json.dumps(list(decision.reason_codes), ensure_ascii=False),
                _iso(decision.decided_at),
            ),
            "editorial upsert",
        )
        stored = self.get(decision.id)
        if stored is None:  # pragma: no cover - defensive
            raise _wrap("editorial upsert")
        return stored

    def get(self, decision_id: str) -> Optional[EditorialDecision]:
        row = _safe_execute(
            self.conn,
            "SELECT * FROM editorial_decision WHERE id = ?",
            (decision_id,),
            "editorial read",
        ).fetchone()
        return self._from_row(row) if row else None

    def list_for_dossier(self, dossier_id: str) -> List[EditorialDecision]:
        rows = _safe_execute(
            self.conn,
            "SELECT * FROM editorial_decision WHERE dossier_id = ? "
            "ORDER BY decided_at DESC, id",
            (dossier_id,),
            "editorial list",
        ).fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row) -> EditorialDecision:
        return EditorialDecision(
            id=row["id"],
            dossier_id=row["dossier_id"],
            policy_version=row["policy_version"],
            input_hash=row["input_hash"],
            decision=row["decision"],
            reason_codes=tuple(json.loads(row["reason_codes"])),
            decided_at=_parse_dt(row["decided_at"]),
        )
