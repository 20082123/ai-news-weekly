"""Integration tests for the phase 2C3 event-candidate pipeline."""

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
from ai_signal.pipeline.event_candidate import record_event_candidate  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
