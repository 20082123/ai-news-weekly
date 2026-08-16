"""Integration tests for the creator-centric hub (choice / gaps / feedback)."""

import io
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.cli import main  # noqa: E402
from ai_signal.domain.models import (  # noqa: E402
    ResearchDossier,
    ResearchFact,
)
from ai_signal.pipeline.creator import (  # noqa: E402
    assess_choice_gaps,
    record_choice,
    record_choice_feedback,
)
from ai_signal.storage import sqlite as S  # noqa: E402
from ai_signal.storage.research_repositories import (  # noqa: E402
    ResearchDossierRepository,
    ResearchFactRepository,
)

TS = "2026-08-15T08:00:00+00:00"


class CreatorLoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai_creator_")
        self.db = os.path.join(self.tmp, "test.db")
        S.initialize_database(self.db)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_event_and_dossier(self, event_id="e" * 64, facts=()):
        conn = S._open(self.db)
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO event_candidate (id, signal_type, subject, "
            "change_summary, affected_audience, work_impact_hypothesis, "
            "missing_evidence, research_priority, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (event_id, "economics_access", "测试事件", "变化待研究",
             "开发者", "选工具", "[]", 50, TS, TS),
        )
        dossier = ResearchDossier(
            event_candidate_id=event_id,
            summary_judgment="机器草案",
            timeline=(),
            target_audience="开发者",
            job_to_be_done="选工具",
            limits_unknowns=(),
            forbidden_claims=(),
            needs_testing=False,
            test_plan=(),
            bundle_hash="b" * 64,
        )
        stored = ResearchDossierRepository(conn).insert_or_get(dossier)
        for kind, text, source_kind, url in facts:
            ResearchFactRepository(conn).insert_or_get(ResearchFact(
                dossier_id=stored.id, kind=kind, text=text,
                source_kind=source_kind, source_url=url,
            ))
        conn.execute("COMMIT")
        conn.close()

    def test_pick_idempotent(self):
        conn = S._open(self.db)
        conn.execute("BEGIN")
        first = record_choice(conn, "2026-W33", "DeepSeek 涨价")
        second = record_choice(conn, "2026-W33", "DeepSeek 涨价")
        conn.execute("COMMIT")
        count = conn.execute("SELECT COUNT(*) FROM creator_choice").fetchone()[0]
        conn.close()
        self.assertEqual(first.id, second.id)
        self.assertEqual(count, 1)

    def test_gaps_unlinked_choice(self):
        conn = S._open(self.db)
        conn.execute("BEGIN")
        choice = record_choice(conn, "2026-W33", "随便一个想法")
        conn.execute("COMMIT")
        report = assess_choice_gaps(conn, choice)
        conn.close()
        self.assertFalse(report.linked_event)
        self.assertEqual(len(report.gaps), 5)  # every gap open

    def test_gaps_shrink_with_dossier_facts(self):
        event_id = "e" * 64
        self._seed_event_and_dossier(
            event_id,
            facts=[
                ("fact", "发布 v0.3.0", "github_release",
                 "https://github.com/x/y/releases/tag/v0.3.0"),
                ("fact", "官方价目", "official_page",
                 "https://api-docs.deepseek.com/quick_start/pricing"),
                ("fact", "用户反馈", "manual",
                 "https://www.reddit.com/r/x/comments/1"),
            ],
        )
        conn = S._open(self.db)
        conn.execute("BEGIN")
        choice = record_choice(conn, "2026-W33", "DeepSeek 涨价", event_id)
        conn.execute("COMMIT")
        report = assess_choice_gaps(conn, choice)
        conn.close()
        self.assertTrue(report.linked_event)
        self.assertTrue(report.has_dossier)
        gap_keys = [gap for gap, _, _ in report.gaps]
        self.assertNotIn("official_confirmation", gap_keys)
        self.assertNotIn("technical_implementation", gap_keys)
        self.assertNotIn("user_reality", gap_keys)
        self.assertIn("early_signal", gap_keys)
        self.assertIn("personal_testing", gap_keys)

    def test_feedback_recorded_and_status_updated(self):
        conn = S._open(self.db)
        conn.execute("BEGIN")
        choice = record_choice(conn, "2026-W33", "DeepSeek 涨价")
        conn.execute("COMMIT")
        conn.close()
        conn = S._open(self.db)
        conn.execute("BEGIN")
        feedback = record_choice_feedback(
            conn, choice.id, "adopted", "数据不错，继续这类",
            usefulness=5, published_url="https://x.com/me/status/1",
        )
        from ai_signal.pipeline.creator import CreatorChoiceRepository

        CreatorChoiceRepository(conn).update_status(choice.id, "published")
        conn.execute("COMMIT")
        conn.close()
        self.assertEqual(feedback.target_type, "creator_choice")
        self.assertEqual(feedback.target_id, choice.id)
        conn = S._open(self.db)
        try:
            row = conn.execute(
                "SELECT decision, usefulness FROM feedback "
                "WHERE target_type = 'creator_choice'"
            ).fetchone()
            status = conn.execute(
                "SELECT status FROM creator_choice WHERE id = ?", (choice.id,)
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual((row["decision"], row["usefulness"]),
                         ("adopted", 5))
        self.assertEqual(status, "published")


class CreatorChoiceCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai_creator_cli_")
        self.db = os.path.join(self.tmp, "test.db")
        S.initialize_database(self.db)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, argv):
        out = io.StringIO()
        code = main(argv, out)
        return code, out.getvalue()

    def test_list_without_week_key_lists_all_weeks(self):
        conn = S._open(self.db)
        conn.execute("BEGIN")
        from ai_signal.pipeline.creator import record_choice

        record_choice(conn, "2026-W32", "上周选题")
        record_choice(conn, "2026-W33", "本周选题")
        conn.execute("COMMIT")
        conn.close()
        code, text = self._run(["choice", "list", "--db-path", self.db])
        self.assertEqual(code, 0)
        self.assertIn("2026-W32", text)
        self.assertIn("上周选题", text)
        self.assertIn("2026-W33", text)
        self.assertIn("本周选题", text)

    def test_pick_then_gaps_roundtrip(self):
        code, text = self._run([
            "choice", "pick", "--db-path", self.db,
            "--week-key", "2026-W33", "--subject", "DeepSeek 涨价",
        ])
        self.assertEqual(code, 0)
        self.assertIn("status: chosen", text)
        code, text = self._run([
            "choice", "gaps", "--db-path", self.db,
            "--week-key", "2026-W33", "--subject", "DeepSeek 涨价",
        ])
        self.assertEqual(code, 0)
        self.assertIn("证据缺口清单", text)
        self.assertIn("official_confirmation", text)


if __name__ == "__main__":
    unittest.main()
