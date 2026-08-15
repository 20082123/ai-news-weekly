"""Repositories for the phase 2C2 GitHub discovery tables (migration 0005).

* :class:`GitHubDiscoveryRunRepository` - insert / read / finalize a policy run;
* :class:`GitHubDiscoveryProbeRunRepository` - insert / read / record a probe
  outcome (including ``blocked`` outcomes that made no network request);
* :class:`GitHubScopeBindingRepository` - claim and read the immutable
  scope_key -> spec_hash binding;
* :class:`GitHubCandidateSelectionRepository` - the final per-run research
  queue selection.

Rules (mirroring the other storage modules):

* all SQL is parameterized;
* repositories never call ``COMMIT`` - the caller owns the transaction;
* every database error is surfaced as :class:`StorageError`;
* identical input re-runs insert nothing (idempotent upserts);
* the scope binding is immutable: ``insert_or_get`` never overwrites an
  existing ``spec_hash``.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import List, Optional

from ..domain.models import (
    GitHubCandidateSelection,
    GitHubDiscoveryProbeRun,
    GitHubDiscoveryRun,
    GitHubScopeBinding,
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
    return StorageError("github discovery storage %s failed" % operation)


def _safe_execute(conn, sql: str, params, operation: str):
    try:
        return conn.execute(sql, params)
    except StorageError:
        raise
    except Exception as exc:  # noqa: BLE001 - surface as StorageError
        raise _wrap(operation) from exc


class GitHubDiscoveryRunRepository:
    def __init__(self, conn):
        self.conn = conn

    def insert(self, run: GitHubDiscoveryRun) -> GitHubDiscoveryRun:
        _safe_execute(
            self.conn,
            "INSERT INTO github_discovery_run "
            "(id, policy_id, policy_hash, week_key, lane, candidate_limit, "
            " research_budget, status, started_at, finished_at, warnings, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run.id,
                run.policy_id,
                run.policy_hash,
                run.week_key,
                run.lane,
                run.candidate_limit,
                run.research_budget,
                run.status,
                _iso(run.started_at),
                _iso(run.finished_at),
                json.dumps(list(run.warnings), ensure_ascii=False),
                _iso(run.created_at),
            ),
            "discovery run insert",
        )
        return run

    def get(self, run_id: str) -> Optional[GitHubDiscoveryRun]:
        row = _safe_execute(
            self.conn,
            "SELECT * FROM github_discovery_run WHERE id = ?",
            (run_id,),
            "discovery run read",
        ).fetchone()
        return self._from_row(row) if row else None

    def update_status(
        self,
        run_id: str,
        status: str,
        *,
        warnings: tuple = (),
        finished_at=None,
    ) -> None:
        """Finalize a run: set status and (optionally) aggregate warnings."""
        _safe_execute(
            self.conn,
            "UPDATE github_discovery_run SET status = ?, warnings = ?, finished_at = ? "
            "WHERE id = ?",
            (
                status,
                json.dumps(list(warnings), ensure_ascii=False),
                _iso(finished_at),
                run_id,
            ),
            "discovery run finalize",
        )

    @staticmethod
    def _from_row(row) -> GitHubDiscoveryRun:
        return GitHubDiscoveryRun(
            id=row["id"],
            policy_id=row["policy_id"],
            policy_hash=row["policy_hash"],
            week_key=row["week_key"],
            lane=row["lane"],
            candidate_limit=int(row["candidate_limit"]),
            research_budget=int(row["research_budget"]),
            status=row["status"],
            started_at=_parse_dt(row["started_at"]),
            finished_at=_parse_dt(row["finished_at"]),
            warnings=tuple(json.loads(row["warnings"])),
            created_at=_parse_dt(row["created_at"]),
        )


class GitHubDiscoveryProbeRunRepository:
    def __init__(self, conn):
        self.conn = conn

    def insert(self, probe_run: GitHubDiscoveryProbeRun) -> GitHubDiscoveryProbeRun:
        _safe_execute(
            self.conn,
            "INSERT INTO github_discovery_probe_run "
            "(id, discovery_run_id, probe_id, kind, lane, scope_key, spec_hash, "
            " priority, collection_run_id, status, item_count, warning_count, "
            " warnings, started_at, finished_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                probe_run.id,
                probe_run.discovery_run_id,
                probe_run.probe_id,
                probe_run.kind,
                probe_run.lane,
                probe_run.scope_key,
                probe_run.spec_hash,
                probe_run.priority,
                probe_run.collection_run_id,
                probe_run.status,
                probe_run.item_count,
                probe_run.warning_count,
                json.dumps(list(probe_run.warnings), ensure_ascii=False),
                _iso(probe_run.started_at),
                _iso(probe_run.finished_at),
                _iso(probe_run.created_at),
            ),
            "probe run insert",
        )
        return probe_run

    def get(self, probe_run_id: str) -> Optional[GitHubDiscoveryProbeRun]:
        row = _safe_execute(
            self.conn,
            "SELECT * FROM github_discovery_probe_run WHERE id = ?",
            (probe_run_id,),
            "probe run read",
        ).fetchone()
        return self._from_row(row) if row else None

    def update_outcome(
        self,
        probe_run_id: str,
        *,
        status: str,
        collection_run_id=None,
        item_count: int = 0,
        warnings: tuple = (),
        finished_at=None,
    ) -> None:
        """Record a probe outcome; ``blocked`` outcomes never reach the network."""
        _safe_execute(
            self.conn,
            "UPDATE github_discovery_probe_run SET status = ?, collection_run_id = ?, "
            " item_count = ?, warning_count = ?, warnings = ?, finished_at = ? "
            "WHERE id = ?",
            (
                status,
                collection_run_id,
                item_count,
                len(warnings),
                json.dumps(list(warnings), ensure_ascii=False),
                _iso(finished_at),
                probe_run_id,
            ),
            "probe run outcome",
        )

    def list_for_run(self, discovery_run_id: str) -> List[GitHubDiscoveryProbeRun]:
        rows = _safe_execute(
            self.conn,
            "SELECT * FROM github_discovery_probe_run WHERE discovery_run_id = ? "
            "ORDER BY priority, probe_id",
            (discovery_run_id,),
            "probe run list",
        ).fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row) -> GitHubDiscoveryProbeRun:
        return GitHubDiscoveryProbeRun(
            id=row["id"],
            discovery_run_id=row["discovery_run_id"],
            probe_id=row["probe_id"],
            kind=row["kind"],
            lane=row["lane"],
            scope_key=row["scope_key"],
            spec_hash=row["spec_hash"],
            priority=int(row["priority"]),
            collection_run_id=row["collection_run_id"],
            status=row["status"],
            item_count=int(row["item_count"]),
            warning_count=int(row["warning_count"]),
            warnings=tuple(json.loads(row["warnings"])),
            started_at=_parse_dt(row["started_at"]),
            finished_at=_parse_dt(row["finished_at"]),
            created_at=_parse_dt(row["created_at"]),
        )


class GitHubScopeBindingRepository:
    """The immutable scope_key -> spec_hash binding.

    ``insert_or_get`` never overwrites an existing binding: the first claim
    wins and later runs must match its ``spec_hash`` or be refused before
    networking.
    """

    def __init__(self, conn):
        self.conn = conn

    def get(self, scope_key: str) -> Optional[GitHubScopeBinding]:
        row = _safe_execute(
            self.conn,
            "SELECT * FROM github_discovery_scope_binding WHERE scope_key = ?",
            (scope_key,),
            "scope binding read",
        ).fetchone()
        return self._from_row(row) if row else None

    def insert_or_get(self, binding: GitHubScopeBinding) -> GitHubScopeBinding:
        _safe_execute(
            self.conn,
            "INSERT INTO github_discovery_scope_binding "
            "(scope_key, probe_id, policy_id, spec_hash, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(scope_key) DO NOTHING",
            (
                binding.scope_key,
                binding.probe_id,
                binding.policy_id,
                binding.spec_hash,
                _iso(binding.created_at),
                _iso(binding.updated_at),
            ),
            "scope binding insert",
        )
        stored = self.get(binding.scope_key)
        if stored is None:  # pragma: no cover - defensive
            raise _wrap("scope binding insert")
        return stored

    @staticmethod
    def _from_row(row) -> GitHubScopeBinding:
        return GitHubScopeBinding(
            scope_key=row["scope_key"],
            probe_id=row["probe_id"],
            policy_id=row["policy_id"],
            spec_hash=row["spec_hash"],
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )


class GitHubCandidateSelectionRepository:
    def __init__(self, conn):
        self.conn = conn

    def insert_or_get(
        self, selection: GitHubCandidateSelection
    ) -> GitHubCandidateSelection:
        """Insert the final per-run selection, or return the existing row.

        Idempotent per ``(discovery_run_id, candidate_id)``: one candidate has
        exactly one final selection per policy run.
        """
        _safe_execute(
            self.conn,
            "INSERT INTO github_candidate_selection "
            "(id, discovery_run_id, candidate_id, winning_discovery_id, "
            " winning_assessment_id, selection_rank, qualification_decision, "
            " queue_state, budget_reason, ecosystem_target, relation_kind, "
            " relation_field, relation_raw_signal_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(discovery_run_id, candidate_id) DO NOTHING",
            (
                selection.id,
                selection.discovery_run_id,
                selection.candidate_id,
                selection.winning_discovery_id,
                selection.winning_assessment_id,
                selection.selection_rank,
                selection.qualification_decision,
                selection.queue_state,
                selection.budget_reason,
                selection.ecosystem_target,
                selection.relation_kind,
                selection.relation_field,
                selection.relation_raw_signal_id,
                _iso(selection.created_at),
            ),
            "selection upsert",
        )
        stored = self.get(selection.id)
        if stored is None:  # pragma: no cover - defensive
            raise _wrap("selection upsert")
        return stored

    def get(self, selection_id: str) -> Optional[GitHubCandidateSelection]:
        row = _safe_execute(
            self.conn,
            "SELECT * FROM github_candidate_selection WHERE id = ?",
            (selection_id,),
            "selection read",
        ).fetchone()
        return self._from_row(row) if row else None

    def list_for_run(self, discovery_run_id: str) -> List[GitHubCandidateSelection]:
        rows = _safe_execute(
            self.conn,
            "SELECT * FROM github_candidate_selection WHERE discovery_run_id = ? "
            "ORDER BY selection_rank, candidate_id",
            (discovery_run_id,),
            "selection list",
        ).fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row) -> GitHubCandidateSelection:
        return GitHubCandidateSelection(
            id=row["id"],
            discovery_run_id=row["discovery_run_id"],
            candidate_id=row["candidate_id"],
            winning_discovery_id=row["winning_discovery_id"],
            winning_assessment_id=row["winning_assessment_id"],
            selection_rank=int(row["selection_rank"]),
            qualification_decision=row["qualification_decision"],
            queue_state=row["queue_state"],
            budget_reason=row["budget_reason"],
            ecosystem_target=row["ecosystem_target"],
            relation_kind=row["relation_kind"],
            relation_field=row["relation_field"],
            relation_raw_signal_id=row["relation_raw_signal_id"],
            created_at=_parse_dt(row["created_at"]),
        )
