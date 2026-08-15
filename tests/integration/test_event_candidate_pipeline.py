"""Integration tests for the phase 2C3 event-candidate pipeline."""

import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.domain.models import (  # noqa: E402
    EventCandidate,
    EventCandidateSourceRef,
)
from ai_signal.pipeline.event_candidate import (  # noqa: E402
    promote_github_queue,
    record_event_candidate,
)
from ai_signal.storage import sqlite as S  # noqa: E402
from ai_signal.storage.event_candidate_repositories import (  # noqa: E402
    EventCandidateRepository,
    EventCandidateSourceRefRepository,
)


def _candidate(**overrides):
    fields = {
        "signal_type": "economics_access",
        "subject": "DeepSeek V4 API",
        "change_summary": "峰谷计价生效",
        "affected_audience": "API 开发者",
        "work_impact_hypothesis": "成本重算",
        "research_priority": 90,
        "missing_evidence": ("third_party_cloud_sync",),
    }
    fields.update(overrides)
    return EventCandidate(**fields)


def _ref(candidate, **overrides):
    fields = {
        "event_candidate_id": candidate.id,
        "source_kind": "official_announcement_candidate",
        "ref_id": "deepseek-news260813",
        "ref_label": "DeepSeek V4-Pro GA 公告",
    }
    fields.update(overrides)
    return EventCandidateSourceRef(**fields)


class EventCandidatePipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai_evc_")
        self.db = os.path.join(self.tmp, "test.db")
        S.initialize_database(self.db)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_record_and_idempotent_rerun(self):
        candidate = _candidate()
        conn = S._open(self.db)
        conn.execute("BEGIN")
        first = record_event_candidate(conn, candidate, [_ref(candidate)])
        conn.execute("COMMIT")
        conn.close()
        self.assertEqual(first.candidate.id, candidate.id)
        self.assertEqual(len(first.refs), 1)

        # Same input again: no duplicates, canonical rows returned.
        conn = S._open(self.db)
        conn.execute("BEGIN")
        second = record_event_candidate(conn, candidate, [_ref(candidate)])
        conn.execute("COMMIT")
        counts = conn.execute(
            "SELECT (SELECT COUNT(*) FROM event_candidate), "
            "       (SELECT COUNT(*) FROM event_candidate_source_ref)"
        ).fetchone()
        conn.close()
        self.assertEqual(second.candidate.id, first.candidate.id)
        self.assertEqual((counts[0], counts[1]), (1, 1))

    def test_cross_source_shared_identity(self):
        # The same change recorded with a different source ref still lands on
        # ONE source-independent candidate row.
        candidate = _candidate()
        conn = S._open(self.db)
        conn.execute("BEGIN")
        record_event_candidate(conn, candidate, [_ref(candidate)])
        github_ref = _ref(
            candidate,
            source_kind="github_repository_candidate",
            ref_id="cand-harness",
            ref_label="deepseek-ai/deepseek-harness",
        )
        record_event_candidate(conn, candidate, [github_ref])
        conn.execute("COMMIT")
        counts = conn.execute(
            "SELECT (SELECT COUNT(*) FROM event_candidate), "
            "       (SELECT COUNT(*) FROM event_candidate_source_ref)"
        ).fetchone()
        conn.close()
        self.assertEqual((counts[0], counts[1]), (1, 2))

    def test_refs_repointed_to_canonical_candidate(self):
        candidate = _candidate()
        # A ref built with the caller's identity guess is normalized to the
        # canonical stored candidate id (identity is deterministic anyway).
        wrong_id_ref = EventCandidateSourceRef(
            event_candidate_id="f" * 64,
            source_kind="official_announcement_candidate",
            ref_id="deepseek-news260813",
            ref_label="DeepSeek V4-Pro GA 公告",
        )
        conn = S._open(self.db)
        conn.execute("BEGIN")
        record = record_event_candidate(conn, candidate, [wrong_id_ref])
        conn.execute("COMMIT")
        conn.close()
        self.assertEqual(record.refs[0].event_candidate_id, candidate.id)

    def test_rollback_on_storage_failure(self):
        candidate = _candidate()
        conn = S._open(self.db)
        conn.execute("BEGIN")
        # The candidate row lands inside the same transaction as the refs...
        record_event_candidate(conn, candidate)
        original = EventCandidateSourceRefRepository.insert_or_get

        def boom(self, ref):
            raise S.StorageError("injected failure")

        EventCandidateSourceRefRepository.insert_or_get = boom
        try:
            with self.assertRaises(S.StorageError):
                record_event_candidate(conn, candidate, [_ref(candidate)])
            conn.execute("ROLLBACK")
            conn.close()
        finally:
            EventCandidateSourceRefRepository.insert_or_get = original
        # ...so the whole transaction rolled back: no half-written rows.
        conn = S._open(self.db)
        count = conn.execute("SELECT COUNT(*) FROM event_candidate").fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

    def test_list_ordered_by_priority_desc(self):
        conn = S._open(self.db)
        conn.execute("BEGIN")
        for priority, subject in ((40, "低优先"), (90, "高优先"), (65, "中优先")):
            record_event_candidate(
                conn,
                _candidate(subject=subject, research_priority=priority),
            )
        conn.execute("COMMIT")
        rows = EventCandidateRepository(conn).list()
        conn.close()
        self.assertEqual(
            [row.research_priority for row in rows], [90, 65, 40]
        )

    def test_list_filter_by_type(self):
        conn = S._open(self.db)
        conn.execute("BEGIN")
        record_event_candidate(conn, _candidate())
        record_event_candidate(conn, _candidate(
            signal_type="capability_change", subject="Gemini 3.7 Flash",
            change_summary="工作马模型发布",
        ))
        conn.execute("COMMIT")
        rows = EventCandidateRepository(conn).list("capability_change")
        conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].signal_type, "capability_change")


TS = "2026-08-15T08:00:00+00:00"


class PromoteGitHubQueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai_evc_promo_")
        self.db = os.path.join(self.tmp, "test.db")
        S.initialize_database(self.db)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_queued(self, conn, *, index=0, week="2026-W33", queued=True,
                     title=None):
        """Seed the full GitHub queue chain for one candidate."""
        suffix = str(index)
        title = title or ("example-org/repo-" + suffix)
        run_id = "gdr-" + suffix
        conn.execute(
            "INSERT INTO github_discovery_run (id, policy_id, policy_hash, "
            "week_key, lane, candidate_limit, research_budget, status, "
            "started_at, finished_at, warnings, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, "test-v1", "a" * 64, week, "emerging", 50, 5, "success",
             TS, TS, "[]", TS),
        )
        conn.execute(
            "INSERT INTO collection_run (id, week_key, started_at, status, "
            "config_snapshot, created_at) VALUES (?,?,?,?,?,?)",
            ("cr-" + suffix, week, TS, "success", "{}", TS),
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
             title, "https://github.com/" + title, TS, TS, TS, TS),
        )
        conn.execute(
            "INSERT INTO candidate_discovery (id, candidate_id, week_key, "
            "scope_key, lane, raw_signal_id, observed_at, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("disc-" + suffix, "cand-" + suffix, week, "ghp-t-v1-q1",
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
        conn.execute(
            "INSERT INTO github_candidate_selection (id, discovery_run_id, "
            "candidate_id, winning_discovery_id, winning_assessment_id, "
            "selection_rank, qualification_decision, queue_state, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("sel-" + suffix, run_id, "cand-" + suffix, "disc-" + suffix,
             "assess-" + suffix, index, "research",
             "queued" if queued else "over_budget", TS),
        )
        return "cand-" + suffix

    def test_promote_creates_drafts_with_refs(self):
        conn = S._open(self.db)
        conn.execute("BEGIN")
        self._seed_queued(conn, index=0)
        self._seed_queued(conn, index=1)
        self._seed_queued(conn, index=2, queued=False)  # over_budget -> skip
        result = promote_github_queue(conn, "2026-W33")
        conn.execute("COMMIT")
        conn.close()
        self.assertEqual(result.processed, 2)   # queued only
        self.assertEqual(result.promoted, 2)
        self.assertEqual(result.refs_added, 2)
        conn = S._open(self.db)
        try:
            rows = conn.execute(
                "SELECT subject, change_summary, affected_audience, "
                "work_impact_hypothesis, research_priority, missing_evidence "
                "FROM event_candidate ORDER BY subject"
            ).fetchall()
            self.assertEqual(len(rows), 2)
            for row in rows:
                self.assertEqual(row["change_summary"], "具体变化待研究（机器草案）")
                self.assertEqual(row["affected_audience"], "待人工确认")
                self.assertEqual(row["work_impact_hypothesis"], "待人工确认")
                self.assertEqual(row["research_priority"], 50)
                self.assertIn("specific_event", json.loads(row["missing_evidence"]))
            refs = conn.execute(
                "SELECT source_kind, ref_id, ref_label FROM "
                "event_candidate_source_ref ORDER BY ref_id"
            ).fetchall()
            self.assertEqual(len(refs), 2)
            for ref in refs:
                self.assertEqual(ref["source_kind"], "github_repository_candidate")
                self.assertEqual(ref["ref_id"], ref["ref_label"].replace("example-org/repo-", "cand-"))
        finally:
            conn.close()

    def test_promote_idempotent_and_skips_referenced(self):
        conn = S._open(self.db)
        conn.execute("BEGIN")
        cand_a = self._seed_queued(conn, index=0)
        self._seed_queued(conn, index=1)
        # Manual seed (like the Golden Set): A is already referenced.
        manual = _candidate(subject="example-org/repo-0")
        record_event_candidate(conn, manual, [_ref(
            manual,
            source_kind="github_repository_candidate",
            ref_id=cand_a,
            ref_label="example-org/repo-0",
        )])
        first = promote_github_queue(conn, "2026-W33")
        second = promote_github_queue(conn, "2026-W33")
        conn.execute("COMMIT")
        conn.close()
        self.assertEqual(first.promoted, 1)          # only repo-1
        self.assertEqual(first.already_promoted, 1)  # repo-0 skipped
        self.assertEqual(second.promoted, 0)
        self.assertEqual(second.already_promoted, 2)
        conn = S._open(self.db)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM event_candidate"
            ).fetchone()[0]
            ref_count = conn.execute(
                "SELECT COUNT(*) FROM event_candidate_source_ref "
                "WHERE source_kind = 'github_repository_candidate'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 2)      # manual + one promoted draft
        self.assertEqual(ref_count, 2)

    def test_promote_invalid_week_raises(self):
        conn = S._open(self.db)
        with self.assertRaises(ValueError):
            promote_github_queue(conn, "not-a-week")
        conn.close()


if __name__ == "__main__":
    unittest.main()
