"""Integration tests for the ``materialize github`` CLI command."""

import hashlib
import json
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
from ai_signal.storage import sqlite as S  # noqa: E402

TS = "2026-08-14T00:00:00+00:00"


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _gh_payload(repo_id, stars, updated_at):
    return {
        "id": repo_id,
        "full_name": "github-org/example-%d" % repo_id,
        "html_url": "https://github.com/github-org/example-%d" % repo_id,
        "description": "an example repository",
        "language": "Python",
        "stargazers_count": stars,
        "forks_count": 1,
        "topics": ["example"],
        "pushed_at": "2026-08-01T00:00:00+00:00",
        "updated_at": updated_at,
    }


def _seed(db_path, specs):
    """specs: list of (run_id, scope_key, repo_payload)."""
    S.initialize_database(db_path)
    conn = S._open(db_path)
    conn.execute("BEGIN")
    seen = set()
    for run_id, scope_key, repo_payload in specs:
        if (run_id, scope_key) not in seen:
            seen.add((run_id, scope_key))
            conn.execute(
                "INSERT INTO collection_run (id, week_key, started_at, status, "
                "config_snapshot, created_at) VALUES (?,?,?,?,?,?)",
                (run_id, "2026-W33", TS, "success", "{}", TS),
            )
            conn.execute(
                "INSERT INTO source_run (id, collection_run_id, source, scope_key, "
                "source_version, status, started_at, finished_at, item_count, "
                "warning_count, warnings, cursor_advanced, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("sr-" + run_id + scope_key, run_id, "github", scope_key,
                 "github-rest-v1", "success", TS, TS, 1, 0, "[]", 0, TS),
            )
        external_id = str(repo_payload["id"])
        payload = json.dumps(repo_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        psha = _sha(payload)
        raw_id = _sha("gh" + external_id + psha + run_id)
        conn.execute(
            "INSERT INTO raw_signal (id, collection_run_id, source, external_id, "
            "payload, payload_sha256, source_version, collected_at, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (raw_id, run_id, "github", external_id, payload, psha, "github-rest-v1", TS, TS),
        )
        conn.execute(
            "INSERT INTO raw_signal_observation (source_run_id, raw_signal_id, "
            "observed_at, created_at) VALUES (?,?,?,?)",
            ("sr-" + run_id + scope_key, raw_id, TS, TS),
        )
    conn.execute("COMMIT")
    conn.close()


class MaterializeCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai_mat_cli_")
        self.db = os.path.join(self.tmp, "test.db")
        self.output = pathlib.Path(self.tmp) / "out"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, extra):
        argv = [
            "materialize", "github",
            "--db-path", self.db,
            "--week-key", "2026-W33",
            "--scope-key", "ai-agents-v1",
            "--output-root", str(self.output),
            "--limit", "10",
        ] + list(extra)
        buf = StringIO()
        rc = cli.main(argv, out=buf)
        return rc, buf.getvalue()

    def test_missing_allow_output_write_returns_four(self):
        _seed(self.db, [("r1", "ai-agents-v1", _gh_payload(1, 10, TS))])
        rc, output = self._run([])
        self.assertEqual(rc, 4)
        self.assertIn("output write not allowed", output)
        self.assertFalse((self.output / "Inbox").exists())

    def test_success_writes_markdown(self):
        _seed(self.db, [
            ("r1", "ai-agents-v1", _gh_payload(1, 10, TS)),
            ("r1", "ai-agents-v1", _gh_payload(2, 20, TS)),
        ])
        rc, output = self._run(["--allow-output-write"])
        self.assertEqual(rc, 0)
        self.assertIn("processed: 2", output)
        self.assertIn("packs: 2", output)
        inbox = self.output / "Inbox"
        self.assertTrue(inbox.exists())
        self.assertEqual(len(list(inbox.glob("*.md"))), 2)

    def test_invalid_scope_returns_two(self):
        _seed(self.db, [("r1", "ai-agents-v1", _gh_payload(1, 10, TS))])
        rc, _ = self._run(["--scope-key", "UPPER", "--allow-output-write"])
        self.assertEqual(rc, 2)

    def test_invalid_week_key_returns_two(self):
        _seed(self.db, [("r1", "ai-agents-v1", _gh_payload(1, 10, TS))])
        rc, _ = self._run(["--week-key", "bad", "--allow-output-write"])
        self.assertEqual(rc, 2)

    def test_output_has_no_sensitive_data(self):
        _seed(self.db, [("r1", "ai-agents-v1", _gh_payload(1, 10, TS))])
        _, output = self._run(["--allow-output-write"])
        self.assertNotIn("ai-agents-v1", output)
        self.assertNotIn("github.com", output)
        self.assertNotIn(str(self.output), output)
        self.assertNotIn(self.db, output)

    def test_scope_isolation_only_packages_requested_scope(self):
        # scope-b pre-exists; materializing scope-a must not repack scope-b.
        _seed(self.db, [
            ("r1", "ai-agents-v1", _gh_payload(1, 10, TS)),
            ("r2", "other-scope-v1", _gh_payload(2, 20, TS)),
        ])
        rc, output = self._run(["--allow-output-write"])
        self.assertEqual(rc, 0)
        self.assertIn("processed: 1", output)
        self.assertIn("packs: 1", output)
        inbox = self.output / "Inbox"
        self.assertEqual(len(list(inbox.glob("*.md"))), 1)

    def test_markdown_failure_keeps_pack_and_reports_output_error(self):
        import ai_signal.outputs.markdown as md_module

        _seed(self.db, [
            ("r1", "ai-agents-v1", _gh_payload(1, 10, TS)),
            ("r1", "ai-agents-v1", _gh_payload(2, 20, TS)),
        ])
        original = md_module.publish_pack_markdown
        calls = {"n": 0}

        def fail_second(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise md_module.MarkdownPublishError("injected")
            return original(*args, **kwargs)

        md_module.publish_pack_markdown = fail_second
        try:
            rc, output = self._run(["--allow-output-write"])
        finally:
            md_module.publish_pack_markdown = original

        self.assertEqual(rc, 5)
        self.assertIn("output error", output)
        self.assertNotIn("database error", output)
        # DB packs are committed (2), but only 1 markdown file exists.
        conn = S._open(self.db)
        try:
            packs = conn.execute("SELECT COUNT(*) FROM material_pack").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(packs, 2)
        inbox = self.output / "Inbox"
        self.assertEqual(len(list(inbox.glob("*.md"))), 1)

        # A re-run safely补写 the missing file.
        rc2, _ = self._run(["--allow-output-write"])
        self.assertEqual(rc2, 0)
        self.assertEqual(len(list(inbox.glob("*.md"))), 2)

    def test_limit_limits_pack_count(self):
        _seed(self.db, [
            ("r1", "ai-agents-v1", _gh_payload(1, 10, TS)),
            ("r1", "ai-agents-v1", _gh_payload(2, 20, TS)),
        ])
        rc, output = self._run(["--limit", "1", "--allow-output-write"])
        self.assertEqual(rc, 0)
        self.assertIn("packs: 1", output)
        self.assertEqual(len(list((self.output / "Inbox").glob("*.md"))), 1)


if __name__ == "__main__":
    unittest.main()
