"""Integration tests for the phase 2C2 GitHub discovery repositories."""

import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.domain.models import (  # noqa: E402
    GitHubCandidateSelection,
    GitHubDiscoveryProbeRun,
    GitHubDiscoveryRun,
    GitHubScopeBinding,
)
from ai_signal.storage import sqlite as S  # noqa: E402
from ai_signal.storage.discovery_repositories import (  # noqa: E402
    GitHubCandidateSelectionRepository,
    GitHubDiscoveryProbeRunRepository,
    GitHubDiscoveryRunRepository,
    GitHubScopeBindingRepository,
)

TS = "2026-08-15T08:00:00+00:00"


def _utc(text):
    return datetime.fromisoformat(text)


class DiscoveryRepositoriesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai_disc_repo_")
        self.db = os.path.join(self.tmp, "test.db")
        S.initialize_database(self.db)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, policy_id="emerging-v1", lane="emerging"):
        return GitHubDiscoveryRun(
            policy_id=policy_id,
            policy_hash="a" * 64,
            week_key="2026-W33",
            lane=lane,
            candidate_limit=100,
            research_budget=8,
            started_at=_utc(TS),
        )

    def _probe_run(self, run_id, probe_id="emerging-v1-q1", scope_key="ghp-e-v1-q1"):
        return GitHubDiscoveryProbeRun(
            discovery_run_id=run_id,
            probe_id=probe_id,
            kind="search",
            lane="emerging",
            scope_key=scope_key,
            spec_hash="b" * 64,
            priority=0,
            started_at=_utc(TS),
        )

    def test_run_insert_and_roundtrip(self):
        run = self._run()
        with S.connect(self.db) as conn:
            conn.execute("BEGIN")
            repo = GitHubDiscoveryRunRepository(conn)
            repo.insert(run)
            stored = repo.get(run.id)
            conn.execute("COMMIT")
        self.assertEqual(stored.policy_id, "emerging-v1")
        self.assertEqual(stored.policy_hash, "a" * 64)
        self.assertEqual(stored.lane, "emerging")
        self.assertEqual(stored.status, "running")

    def test_probe_run_insert_and_outcome(self):
        run = self._run()
        probe_run = self._probe_run(run.id)
        with S.connect(self.db) as conn:
            conn.execute("BEGIN")
            GitHubDiscoveryRunRepository(conn).insert(run)
            probe_repo = GitHubDiscoveryProbeRunRepository(conn)
            probe_repo.insert(probe_run)
            stored = probe_repo.get(probe_run.id)
            self.assertEqual(stored.status, "running")
            probe_repo.update_outcome(
                probe_run.id,
                status="blocked",
                warnings=("SCOPE_SPEC_MISMATCH",),
                finished_at=_utc(TS),
            )
            updated = probe_repo.get(probe_run.id)
            conn.execute("COMMIT")
        self.assertEqual(updated.status, "blocked")
        self.assertEqual(updated.warning_count, 1)
        self.assertEqual(updated.warnings, ("SCOPE_SPEC_MISMATCH",))
        self.assertIsNone(updated.collection_run_id)

    def test_binding_immutable_first_claim_wins(self):
        binding = GitHubScopeBinding(
            scope_key="ghp-e-v1-q1",
            probe_id="emerging-v1-q1",
            policy_id="emerging-v1",
            spec_hash="c" * 64,
            created_at=_utc(TS),
        )
        conflicting = GitHubScopeBinding(
            scope_key="ghp-e-v1-q1",
            probe_id="other-probe",
            policy_id="other-policy",
            spec_hash="d" * 64,
            created_at=_utc(TS),
        )
        with S.connect(self.db) as conn:
            conn.execute("BEGIN")
            repo = GitHubScopeBindingRepository(conn)
            self.assertIsNone(repo.get("ghp-e-v1-q1"))
            first = repo.insert_or_get(binding)
            second = repo.insert_or_get(conflicting)
            conn.execute("COMMIT")
        self.assertEqual(first.spec_hash, "c" * 64)
        # The conflict must NOT overwrite the original claim.
        self.assertEqual(second.spec_hash, "c" * 64)
        self.assertEqual(second.probe_id, "emerging-v1-q1")

    def _seed_candidate_context(self, conn, run_id=None, index=0):
        """Create the FK chain a selection points at.

        Mirrors what the 2C2-B pipeline will produce before selection:
        collection_run -> raw_signal -> candidate -> candidate_discovery ->
        candidate_assessment, plus a github_discovery_run (reused when
        ``run_id`` is given so several candidates share one policy run).
        Returns a dict of the created ids.
        """
        suffix = str(index)
        conn.execute(
            "INSERT INTO collection_run (id, week_key, started_at, status, "
            "config_snapshot, created_at) VALUES (?,?,?,?,?,?)",
            ("cr-" + suffix, "2026-W33", TS, "success", "{}", TS),
        )
        conn.execute(
            "INSERT INTO raw_signal (id, collection_run_id, source, external_id, "
            "payload, payload_sha256, source_version, collected_at, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("raw-" + suffix, "cr-" + suffix, "github", "9000" + suffix, "{}",
             "e" * 64, "github-rest-v1", TS, TS),
        )
        conn.execute(
            "INSERT INTO candidate (id, source, canonical_key, title, url, "
            "first_seen_at, last_seen_at, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("cand-" + suffix, "github", "github:repository:9000" + suffix,
             "example-org/repo-" + suffix, "https://github.com/example-org/repo-" + suffix,
             TS, TS, TS, TS),
        )
        conn.execute(
            "INSERT INTO candidate_discovery (id, candidate_id, week_key, "
            "scope_key, lane, raw_signal_id, observed_at, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("disc-" + suffix, "cand-" + suffix, "2026-W33", "ghp-e-v1-q1",
             "emerging", "raw-" + suffix, TS, TS),
        )
        conn.execute(
            "INSERT INTO candidate_assessment (id, candidate_discovery_id, "
            "policy_version, input_hash, decision, trigger_kind, trigger_summary, "
            "reason_codes, missing_evidence, attributes, assessed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("assess-" + suffix, "disc-" + suffix, "candidate-gate-v2", "f" * 64,
             "research", "repository_snapshot", "summary", "[]", "[]", "{}", TS),
        )
        # The discovery run the selection belongs to (reused when provided).
        if run_id is None:
            run = self._run()
            GitHubDiscoveryRunRepository(conn).insert(run)
            run_id = run.id
        return {
            "run_id": run_id,
            "candidate_id": "cand-" + suffix,
            "discovery_id": "disc-" + suffix,
            "assessment_id": "assess-" + suffix,
            "raw_signal_id": "raw-" + suffix,
        }

    def test_selection_idempotent_per_run_and_candidate(self):
        with S.connect(self.db) as conn:
            conn.execute("BEGIN")
            ctx = self._seed_candidate_context(conn)
            repo = GitHubCandidateSelectionRepository(conn)
            selection = GitHubCandidateSelection(
                discovery_run_id=ctx["run_id"],
                candidate_id=ctx["candidate_id"],
                winning_discovery_id=ctx["discovery_id"],
                winning_assessment_id=ctx["assessment_id"],
                selection_rank=0,
                qualification_decision="research",
                queue_state="queued",
                created_at=_utc(TS),
            )
            first = repo.insert_or_get(selection)
            again = repo.insert_or_get(selection)
            count = conn.execute(
                "SELECT COUNT(*) FROM github_candidate_selection"
            ).fetchone()[0]
            conn.execute("COMMIT")
        self.assertEqual(first.id, selection.id)
        self.assertEqual(again.id, selection.id)
        self.assertEqual(count, 1)

    def test_selection_list_ordered_by_rank(self):
        with S.connect(self.db) as conn:
            conn.execute("BEGIN")
            # Three candidate contexts sharing ONE discovery run.
            ctx0 = self._seed_candidate_context(conn, run_id=None, index=0)
            run_id = ctx0["run_id"]
            ctx1 = self._seed_candidate_context(conn, run_id=run_id, index=1)
            ctx2 = self._seed_candidate_context(conn, run_id=run_id, index=2)
            repo = GitHubCandidateSelectionRepository(conn)
            for rank, ctx_n in ((0, ctx0), (2, ctx2), (1, ctx1)):
                repo.insert_or_get(
                    GitHubCandidateSelection(
                        discovery_run_id=run_id,
                        candidate_id=ctx_n["candidate_id"],
                        winning_discovery_id=ctx_n["discovery_id"],
                        winning_assessment_id=ctx_n["assessment_id"],
                        selection_rank=rank,
                        qualification_decision="research",
                        queue_state="queued" if rank < 2 else "over_budget",
                        budget_reason=None if rank < 2 else "RESEARCH_BUDGET_EXCEEDED",
                        created_at=_utc(TS),
                    )
                )
            rows = repo.list_for_run(run_id)
            conn.execute("COMMIT")
        self.assertEqual([row.selection_rank for row in rows], [0, 1, 2])
        self.assertEqual([row.queue_state for row in rows],
                         ["queued", "queued", "over_budget"])

    def test_selection_relation_fields_roundtrip(self):
        with S.connect(self.db) as conn:
            conn.execute("BEGIN")
            ctx = self._seed_candidate_context(conn)
            selection = GitHubCandidateSelection(
                discovery_run_id=ctx["run_id"],
                candidate_id=ctx["candidate_id"],
                winning_discovery_id=ctx["discovery_id"],
                winning_assessment_id=ctx["assessment_id"],
                selection_rank=0,
                qualification_decision="research",
                queue_state="queued",
                created_at=_utc(TS),
                ecosystem_target="owner/core",
                relation_kind="description_mention",
                relation_field="description",
                relation_raw_signal_id=ctx["raw_signal_id"],
            )
            stored = GitHubCandidateSelectionRepository(conn).insert_or_get(selection)
            conn.execute("COMMIT")
        self.assertEqual(stored.relation_kind, "description_mention")
        self.assertEqual(stored.relation_raw_signal_id, ctx["raw_signal_id"])

    def test_run_finalize_with_warnings(self):
        run = self._run()
        with S.connect(self.db) as conn:
            conn.execute("BEGIN")
            repo = GitHubDiscoveryRunRepository(conn)
            repo.insert(run)
            repo.update_status(
                run.id,
                "partial",
                warnings=("SCOPE_SPEC_MISMATCH",),
                finished_at=_utc(TS),
            )
            stored = repo.get(run.id)
            conn.execute("COMMIT")
        self.assertEqual(stored.status, "partial")
        self.assertEqual(stored.warnings, ("SCOPE_SPEC_MISMATCH",))

    def test_domain_validation_rejects_bad_rows(self):
        with self.assertRaises(ValueError):
            GitHubDiscoveryRun(
                policy_id="emerging-v1",
                policy_hash="short",
                week_key="2026-W33",
                lane="emerging",
                candidate_limit=100,
                research_budget=8,
                started_at=_utc(TS),
            )
        with self.assertRaises(ValueError):
            GitHubCandidateSelection(
                discovery_run_id="run-1",
                candidate_id="cand-1",
                winning_discovery_id="disc-1",
                winning_assessment_id="assess-1",
                selection_rank=-1,
                qualification_decision="research",
                queue_state="queued",
                created_at=_utc(TS),
            )
        with self.assertRaises(ValueError):
            GitHubDiscoveryProbeRun(
                discovery_run_id="run-1",
                probe_id="emerging-v1-q1",
                kind="search",
                lane="emerging",
                scope_key="ghp-e-v1-q1",
                spec_hash="b" * 64,
                priority=0,
                started_at=_utc(TS),
                warnings=("only-one",),
                warning_count=2,
            )


if __name__ == "__main__":
    unittest.main()
