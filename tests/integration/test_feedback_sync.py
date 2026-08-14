"""Integration tests for :mod:`ai_signal.feedback.sync` and the feedback CLI."""

import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from io import StringIO

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal import cli  # noqa: E402
from ai_signal.feedback import sync as sync_module  # noqa: E402
from ai_signal.feedback.sync import sync_feedback  # noqa: E402
from ai_signal.storage import sqlite as S  # noqa: E402

TS = datetime(2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc).isoformat()
_PACK_ID = "a" * 64


def _seed_pack(db_path, pack_id=_PACK_ID, event_id="e1"):
    S.initialize_database(db_path)
    conn = S._open(db_path)
    conn.execute("BEGIN")
    conn.execute(
        "INSERT OR IGNORE INTO event (id, canonical_key, title, created_at, updated_at, payload) "
        "VALUES (?,?,?,?,?,?)",
        (event_id, "github:event:repository:" + event_id, "example/x", TS, TS, "{}"),
    )
    conn.execute(
        "INSERT INTO material_pack (id, week_key, event_id, claim_ids, content, "
        "bundle_hash, created_at) VALUES (?,?,?,?,?,?,?)",
        (pack_id, "2026-W33", event_id, "[]", "{}", "h" * 64, TS),
    )
    conn.execute("COMMIT")
    conn.close()


def _write_md(inbox, pack_id, decision, usefulness=None, reason=None, published_url=None):
    path = inbox / (pack_id + ".md")
    lines = [
        "---",
        "schema_version: 1",
        'target_type: "material_pack"',
        'target_id: "%s"' % pack_id,
        'event_id: "e1"',
        'week_key: "2026-W33"',
        "decision: %s" % ("null" if decision is None else '"%s"' % decision),
    ]
    if reason is not None:
        lines.append('reason: "%s"' % reason)
    if usefulness is not None:
        lines.append("usefulness: %d" % usefulness)
    if published_url is not None:
        lines.append('published_url: "%s"' % published_url)
    lines.append("---")
    lines.append("body")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


class FeedbackSyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai_fb_")
        self.db = os.path.join(self.tmp, "test.db")
        self.inbox = pathlib.Path(self.tmp) / "Inbox"
        self.inbox.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _sync(self):
        conn = S._open(self.db)
        conn.execute("BEGIN")
        result = sync_feedback(conn, self.inbox)
        conn.execute("COMMIT")
        conn.close()
        return result

    def test_inserts_valid_feedback(self):
        _seed_pack(self.db)
        _write_md(self.inbox, _PACK_ID, "adopted", usefulness=4)
        result = self._sync()
        self.assertEqual((result.scanned, result.inserted, result.skipped), (1, 1, 0))

    def test_resync_is_idempotent(self):
        _seed_pack(self.db)
        _write_md(self.inbox, _PACK_ID, "parked")
        self._sync()
        result2 = self._sync()
        self.assertEqual(result2.inserted, 0)
        self.assertEqual(result2.skipped, 1)

    def test_changed_decision_appends_new(self):
        _seed_pack(self.db)
        _write_md(self.inbox, _PACK_ID, "parked")
        self._sync()
        _write_md(self.inbox, _PACK_ID, "rejected", reason="not useful")
        result2 = self._sync()
        self.assertEqual(result2.inserted, 1)

    def test_null_decision_skipped(self):
        _seed_pack(self.db)
        _write_md(self.inbox, _PACK_ID, None)
        result = self._sync()
        self.assertEqual((result.skipped, result.inserted), (1, 0))

    def test_unknown_pack_invalid(self):
        _seed_pack(self.db)
        _write_md(self.inbox, "z" * 64, "adopted")
        result = self._sync()
        self.assertEqual(result.invalid, 1)

    def test_non_https_published_url_invalid(self):
        _seed_pack(self.db)
        _write_md(self.inbox, _PACK_ID, "adopted", published_url="http://example.com")
        result = self._sync()
        self.assertEqual(result.invalid, 1)

    def test_usefulness_out_of_range_invalid(self):
        _seed_pack(self.db)
        _write_md(self.inbox, _PACK_ID, "adopted", usefulness=9)
        result = self._sync()
        self.assertEqual(result.invalid, 1)

    def test_invalid_file_does_not_block_valid(self):
        _seed_pack(self.db)
        _write_md(self.inbox, _PACK_ID, "adopted", usefulness=4)
        _write_md(self.inbox, "z" * 64, "adopted")  # unknown pack
        result = self._sync()
        self.assertEqual(result.inserted, 1)
        self.assertEqual(result.invalid, 1)

    def test_database_error_rolls_back_all(self):
        _seed_pack(self.db, pack_id="a" * 64, event_id="e1")
        _seed_pack(self.db, pack_id="b" * 64, event_id="e2")
        _write_md(self.inbox, "a" * 64, "adopted")
        _write_md(self.inbox, "b" * 64, "parked")  # 2nd distinct feedback
        original = sync_module.FeedbackRepository.insert
        calls = {"n": 0}

        def boom(self, feedback):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise S.StorageError("injected failure")
            return original(self, feedback)

        sync_module.FeedbackRepository.insert = boom
        try:
            conn = S._open(self.db)
            conn.execute("BEGIN")
            with self.assertRaises(S.StorageError):
                sync_feedback(conn, self.inbox)
            conn.execute("ROLLBACK")
            conn.close()
        finally:
            sync_module.FeedbackRepository.insert = original

        conn = S._open(self.db)
        try:
            count = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
            self.assertEqual(count, 0)  # whole transaction rolled back
        finally:
            conn.close()


class FeedbackCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai_fb_cli_")
        self.db = os.path.join(self.tmp, "test.db")
        self.inbox = pathlib.Path(self.tmp) / "Inbox"
        self.inbox.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, extra):
        buf = StringIO()
        rc = cli.main(
            ["feedback", "sync", "--db-path", self.db, "--inbox-dir", str(self.inbox)]
            + list(extra),
            out=buf,
        )
        return rc, buf.getvalue()

    def test_missing_write_gate_returns_four_and_no_db(self):
        rc, output = self._run([])
        self.assertEqual(rc, 4)
        self.assertIn("feedback write not allowed", output)
        self.assertFalse(os.path.exists(self.db))

    def test_success_output_has_no_leak(self):
        _seed_pack(self.db)
        _write_md(self.inbox, _PACK_ID, "adopted", usefulness=4)
        rc, output = self._run(["--allow-feedback-write"])
        self.assertEqual(rc, 0)
        self.assertIn("inserted: 1", output)
        self.assertNotIn(self.db, output)
        self.assertNotIn(str(self.inbox), output)
        self.assertNotIn("example.com", output)
        self.assertNotIn("adopted", output)  # no decision/body leak


if __name__ == "__main__":
    unittest.main()
