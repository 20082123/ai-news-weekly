"""DEC-021 publishing outputs: prompt pack + platform skeleton (offline)."""

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
    EditorialDecision,
    EventCandidate,
    ResearchDossier,
    ResearchFact,
)
from ai_signal.outputs.platform_drafts import (  # noqa: E402
    publish_platform_skeleton,
    publish_prompt_pack,
    render_platform_skeleton,
    render_prompt_pack,
)
from ai_signal.outputs.platform_templates import (  # noqa: E402
    PlatformTemplateError,
    get_platform,
)
from ai_signal.storage import sqlite as S  # noqa: E402
from ai_signal.storage.event_candidate_repositories import (  # noqa: E402
    EventCandidateRepository,
)
from ai_signal.storage.research_repositories import (  # noqa: E402
    EditorialDecisionRepository,
    ResearchDossierRepository,
    ResearchFactRepository,
)

TS = "2026-08-16T08:00:00+00:00"


class PlatformDraftsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai_platform_")
        self.db = os.path.join(self.tmp, "test.db")
        self.out = pathlib.Path(self.tmp) / "out"
        self.out.mkdir()
        S.initialize_database(self.db)
        self._seed()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self):
        conn = S._open(self.db)
        conn.execute("BEGIN")
        event = EventCandidate(
            signal_type="economics_access",
            subject="DeepSeek V4 API",
            change_summary="峰谷计价",
            affected_audience="API 使用者",
            work_impact_hypothesis="成本重算",
            research_priority=90,
        )
        EventCandidateRepository(conn).insert_or_get(event)
        dossier = ResearchDossier(
            event_candidate_id=event.id,
            summary_judgment="机器草案：分峰谷计价。",
            timeline=(),
            target_audience="API 使用者",
            job_to_be_done="成本重算",
            limits_unknowns=("无独立评测",),
            forbidden_claims=("爆火", "所有时段都涨 4.5 倍"),
            needs_testing=False,
            test_plan=(),
            bundle_hash="b" * 64,
        )
        stored = ResearchDossierRepository(conn).insert_or_get(dossier)
        ResearchFactRepository(conn).insert_or_get(ResearchFact(
            dossier_id=stored.id, kind="official_claim",
            text='人民币价目：旧价 6 元 → 高峰 27 元。<div>badge</div>',
            source_kind="official_page",
            source_url="https://api-docs.deepseek.com/zh-cn/quick_start/pricing",
        ))
        decision = EditorialDecision(
            dossier_id=stored.id,
            policy_version="editorial-v1",
            input_hash="a" * 64,
            decision="ready_to_write",
            reason_codes=("first_party_change_evidence",),
        )
        EditorialDecisionRepository(conn).insert_or_get(decision)
        conn.execute("COMMIT")
        conn.close()
        self.event_id = event.id

    def _load(self):
        conn = S._open(self.db)
        try:
            event = EventCandidateRepository(conn).get(self.event_id)
            dossier = ResearchDossierRepository(conn).list_for_event(self.event_id)[0]
            facts = ResearchFactRepository(conn).list_for_dossier(dossier.id)
            decision = EditorialDecisionRepository(conn).list_for_dossier(dossier.id)[0]
        finally:
            conn.close()
        return event, dossier, facts, decision

    def test_prompt_pack_has_guardrails_and_blanks(self):
        event, dossier, facts, decision = self._load()
        text = render_prompt_pack("2026-W33", event, dossier, facts, decision,
                                  get_platform("xiaohongshu"))
        self.assertIn("禁说清单", text)
        self.assertIn("爆火", text)
        self.assertIn("zh-cn/quick_start/pricing", text)
        self.assertIn("标题≤20字", text)
        self.assertIn("② 谁的任务变了：____", text)
        self.assertIn("③ 之前 vs 现在：____", text)
        self.assertIn("配图模板", text)
        self.assertNotIn("<div", text)

    def test_skeleton_has_facts_slots_and_image_plan(self):
        event, dossier, facts, decision = self._load()
        text = render_platform_skeleton("2026-W33", event, dossier, facts,
                                        decision, get_platform("xiaohongshu"))
        self.assertIn("数字块", text)
        self.assertIn("事实清单（只能从这里取数）", text)
        self.assertIn("6 元", text)  # fact carried, display-cleaned
        self.assertNotIn("<div", text)
        self.assertIn("____", text)  # expression slots
        self.assertIn("配图清单", text)
        self.assertIn("红框", text)
        self.assertIn("终审自查 · 禁说清单", text)
        self.assertIn("判定备忘：可直接写", text)

    def test_publishers_write_under_drafts(self):
        event, dossier, facts, decision = self._load()
        pack = publish_prompt_pack(self.out, "2026-W33", event, dossier,
                                   facts, decision)
        skel = publish_platform_skeleton(self.out, "2026-W33", event, dossier,
                                         facts, decision)
        self.assertTrue(pack.exists())
        self.assertTrue(skel.exists())
        self.assertEqual(pack.parent.name, "Drafts")
        self.assertIn("提示包", pack.name)
        self.assertIn("骨架稿", skel.name)
        self.assertTrue(
            str(pack.resolve()).startswith(str(self.out.resolve()))
        )

    def test_unknown_platform_rejected(self):
        with self.assertRaises(PlatformTemplateError):
            get_platform("nope")

    def test_cli_prompt_pack_and_platform_draft(self):
        for sub in ("prompt-pack", "platform-draft"):
            out = io.StringIO()
            code = main(
                [
                    "content", sub,
                    "--db-path", self.db,
                    "--event-id", self.event_id,
                    "--week-key", "2026-W33",
                    "--output-root", str(self.out),
                    "--allow-output-write",
                ],
                out,
            )
            self.assertEqual(code, 0, out.getvalue())
            self.assertIn("written:", out.getvalue())
        drafts = sorted(p.name for p in (self.out / "Drafts").iterdir())
        self.assertEqual(len(drafts), 2)

    def test_cli_rejects_write_without_gate(self):
        out = io.StringIO()
        code = main(
            [
                "content", "prompt-pack",
                "--db-path", self.db,
                "--event-id", self.event_id,
                "--week-key", "2026-W33",
                "--output-root", str(self.out),
            ],
            out,
        )
        self.assertEqual(code, 4)


if __name__ == "__main__":
    unittest.main()
