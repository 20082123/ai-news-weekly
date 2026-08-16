"""Two-round feedback on content briefs (DEC-018).

Round 1 (topic judgement, optional) and round 2 (published outcome,
required after publishing) both land in the ``feedback`` table through
``feedback sync --content-dir``. The deterministic feedback id covers all
filled fields, so filling round 2 later creates a NEW row - evolution stays
auditable.
"""

import os
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.domain.models import (  # noqa: E402
    Feedback,
    content_brief_entity_id,
)
from ai_signal.feedback.sync import (  # noqa: E402
    FeedbackSyncError,
    sync_feedback,
)
from ai_signal.storage import sqlite as S  # noqa: E402
from ai_signal.storage.material_repositories import (  # noqa: E402
    FeedbackRepository,
)

TS = "2026-08-15T08:00:00+00:00"
EVENT_ID = "e" * 64
BRIEF_ID = content_brief_entity_id("2026-W33", EVENT_ID)


def _brief_fm(**overrides) -> str:
    fields = {
        "kind": "content-brief",
        "week_key": "2026-W33",
        "event_candidate_id": EVENT_ID,
        "dossier_id": "d" * 64,
        "brief_id": BRIEF_ID,
        "signal_type": "economics_access",
        "editorial": "ready_to_write",
        "decision": None,
        "reason": None,
        "audience": None,
        "angle": None,
        "usefulness": None,
        "published_url": None,
        "published_at": None,
        "outcome": None,
        "lesson": None,
    }
    fields.update(overrides)
    lines = ["---"]
    for key, value in fields.items():
        if value is None:
            lines.append("%s: null" % key)
        elif isinstance(value, int):
            lines.append("%s: %d" % (key, value))
        else:
            lines.append('%s: "%s"' % (key, value))
    lines.append("---")
    lines.append("")
    lines.append("body")
    return "\n".join(lines)


class TwoRoundFeedbackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai_tworound_")
        self.db = os.path.join(self.tmp, "test.db")
        self.content = pathlib.Path(self.tmp) / "Content"
        self.content.mkdir()
        S.initialize_database(self.db)
        conn = S._open(self.db)
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO event_candidate (id, signal_type, subject, "
            "change_summary, affected_audience, work_impact_hypothesis, "
            "missing_evidence, research_priority, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (EVENT_ID, "economics_access", "DeepSeek 涨价", "峰谷计价",
             "开发者", "成本重算", "[]", 90, TS, TS),
        )
        conn.execute("COMMIT")
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, fm_text):
        (self.content / name).write_text(fm_text, encoding="utf-8")

    def _sync(self):
        conn = S._open(self.db)
        try:
            conn.execute("BEGIN")
            result = sync_feedback(conn, content_dir=self.content)
            conn.execute("COMMIT")
        finally:
            conn.close()
        return result

    def _rows(self):
        conn = S._open(self.db)
        try:
            rows = conn.execute(
                "SELECT * FROM feedback ORDER BY created_at, id"
            ).fetchall()
        finally:
            conn.close()
        return rows

    def test_round1_only_inserts_and_round2_adds_new_row(self):
        self._write("2026-W33 - DeepSeek 涨价.md", _brief_fm(
            decision="adopted", reason="数字硬，值得写", usefulness=5,
        ))
        result = self._sync()
        self.assertEqual((result.scanned, result.inserted),
                         (1, 1))
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["target_type"], "content_brief")
        self.assertEqual(rows[0]["target_id"], BRIEF_ID)
        self.assertEqual(rows[0]["published_at"], None)

        # Days later: the user fills round 2 -> NEW row, old row untouched.
        self._write("2026-W33 - DeepSeek 涨价.md", _brief_fm(
            decision="adopted", reason="数字硬，值得写", usefulness=5,
            published_url="https://x.com/me/status/123",
            published_at="2026-08-20",
            outcome="阅读 2.1k，收藏 300，评论 40 在问峰值时段",
            lesson="峰值时段换算成北京时间更直观",
        ))
        result = self._sync()
        self.assertEqual(result.inserted, 1)
        rows = self._rows()
        self.assertEqual(len(rows), 2)  # audit trail, not mutation
        filled = [r for r in rows if r["published_at"] == "2026-08-20"]
        self.assertEqual(len(filled), 1)
        self.assertEqual(filled[0]["outcome"].startswith("阅读"), True)
        self.assertEqual(filled[0]["target_id"], BRIEF_ID)

    def test_no_decision_is_skipped(self):
        self._write("2026-W33 - DeepSeek 涨价.md", _brief_fm())
        result = self._sync()
        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.inserted, 0)
        self.assertEqual(len(self._rows()), 0)

    def test_brief_id_mismatch_invalid(self):
        self._write("x.md", _brief_fm(brief_id="f" * 64, decision="adopted"))
        result = self._sync()
        self.assertEqual(result.invalid, 1)
        self.assertEqual(result.inserted, 0)

    def test_unknown_event_invalid(self):
        self._write(
            "x.md",
            _brief_fm(event_candidate_id="9" * 64,
                      brief_id=content_brief_entity_id("2026-W33", "9" * 64),
                      decision="adopted"),
        )
        result = self._sync()
        self.assertEqual(result.invalid, 1)

    def test_bad_round2_date_invalid(self):
        self._write("x.md", _brief_fm(decision="adopted",
                                      published_at="2026/08/20"))
        result = self._sync()
        self.assertEqual(result.invalid, 1)
        self.assertEqual(len(self._rows()), 0)

    def test_rejected_round1_recorded(self):
        self._write("x.md", _brief_fm(decision="rejected", reason="不吸引"))
        result = self._sync()
        self.assertEqual(result.inserted, 1)
        self.assertEqual(self._rows()[0]["decision"], "rejected")

    def test_legacy_inbox_still_syncs_alongside(self):
        # Seed a legacy material pack and keep its 2B path working.
        conn = S._open(self.db)
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO material_pack (id, week_key, event_id, claim_ids, "
            "content, bundle_hash, created_at) VALUES (?,?,?,?,?,?,?)",
            ("p" * 64, "2026-W32", None, "[]", "pack", "b" * 64, TS),
        )
        conn.execute("COMMIT")
        conn.close()
        inbox = pathlib.Path(self.tmp) / "Inbox"
        inbox.mkdir()
        (inbox / "pack.md").write_text(
            "---\ntarget_type: material_pack\ntarget_id: %s\n"
            "decision: parked\nreason: 旧的\n---\n" % ("p" * 64),
            encoding="utf-8",
        )
        self._write("2026-W33 - DeepSeek 涨价.md", _brief_fm(
            decision="adopted", usefulness=4,
        ))
        conn = S._open(self.db)
        try:
            conn.execute("BEGIN")
            result = sync_feedback(conn, inbox_dir=inbox,
                                   content_dir=self.content)
            conn.execute("COMMIT")
        finally:
            conn.close()
        self.assertEqual(result.inserted, 2)
        rows = self._rows()
        self.assertEqual({r["target_type"] for r in rows},
                         {"material_pack", "content_brief"})


class TwoRoundModelsTest(unittest.TestCase):
    def test_brief_entity_id_deterministic(self):
        first = content_brief_entity_id("2026-W33", EVENT_ID)
        second = content_brief_entity_id("2026-W33", EVENT_ID)
        other_week = content_brief_entity_id("2026-W34", EVENT_ID)
        self.assertEqual(first, second)
        self.assertNotEqual(first, other_week)
        self.assertEqual(len(first), 64)

    def test_feedback_round2_validation(self):
        from datetime import datetime, timezone

        base = dict(
            target_type="content_brief", target_id=BRIEF_ID,
            decision="adopted",
            created_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        )
        Feedback(**base, published_at="2026-08-20")  # ok
        with self.assertRaises(ValueError):
            Feedback(**base, published_at="2026-13-40")
        with self.assertRaises(ValueError):
            Feedback(**base, published_at="not-a-date")


if __name__ == "__main__":
    unittest.main()
