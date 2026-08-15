"""Integration tests for the ``candidate qualify-github`` CLI command (v2)."""

import hashlib
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import unittest
from io import StringIO

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal import cli  # noqa: E402
from ai_signal.storage import sqlite as S  # noqa: E402

WEEK = "2026-W33"
SCOPE = "emerging-ai-agent-v1"
TS = "2026-08-14T00:00:00+00:00"

# CLI payloads use real github.com URLs: the command qualifies real collected
# GitHub snapshots; the synthetic repos themselves are entirely fictional.
_RESEARCH = {
    "id": 70001,
    "full_name": "example-org/candidate-one",
    "html_url": "https://github.com/example-org/candidate-one",
    "description": "A substantive agent harness description with enough characters to pass the gate.",
    "topics": ["agent", "synthetic"],
    "language": "Python",
    "stargazers_count": 3,
    "forks_count": 0,
    "created_at": "2026-06-01T00:00:00+00:00",
    "updated_at": "2026-08-10T00:00:00+00:00",
    "pushed_at": "2026-08-10T00:00:00+00:00",
    "fork": False,
    "archived": False,
    "disabled": False,
    "is_template": False,
}
_WATCH = {
    "id": 70002,
    "full_name": "example-org/candidate-two",
    "html_url": "https://github.com/example-org/candidate-two",
    "description": "thin",
    "topics": [],
    "stargazers_count": 0,
    "forks_count": 0,
    "created_at": "2020-01-01T00:00:00+00:00",
    "updated_at": "2026-08-10T00:00:00+00:00",
    "pushed_at": "2026-08-10T00:00:00+00:00",
    "fork": False,
    "archived": False,
    "disabled": False,
    "is_template": False,
}
_REJECT = {
    "id": 70003,
    "full_name": "example-org/candidate-three",
    "html_url": "https://github.com/example-org/candidate-three",
    "description": "A substantive agent harness description with enough characters to pass the gate.",
    "topics": ["agent"],
    "stargazers_count": 5,
    "forks_count": 1,
    "created_at": "2026-06-01T00:00:00+00:00",
    "updated_at": "2026-08-10T00:00:00+00:00",
    "pushed_at": "2026-08-10T00:00:00+00:00",
    "fork": True,
    "archived": False,
    "disabled": False,
    "is_template": False,
}


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _seed(db, payloads):
    S.initialize_database(db)
    conn = S._open(db)
    conn.execute("BEGIN")
    conn.execute(
        "INSERT INTO collection_run (id, week_key, started_at, status, "
        "config_snapshot, created_at) VALUES (?,?,?,?,?,?)",
        ("run-1", WEEK, TS, "success", "{}", TS),
    )
    conn.execute(
        "INSERT INTO source_run (id, collection_run_id, source, scope_key, "
        "source_version, status, started_at, finished_at, item_count, "
        "warning_count, warnings, cursor_advanced, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("sr-1", "run-1", "github", SCOPE, "github-rest-v1", "success",
         TS, TS, len(payloads), 0, "[]", 0, TS),
    )
    for payload in payloads:
        external_id = str(payload["id"])
        text = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        psha = _sha(text)
        raw_id = _sha("cli" + external_id + psha)
        conn.execute(
            "INSERT INTO raw_signal (id, collection_run_id, source, external_id, "
            "payload, payload_sha256, source_version, collected_at, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (raw_id, "run-1", "github", external_id, text, psha,
             "github-rest-v1", TS, TS),
        )
        conn.execute(
            "INSERT INTO raw_signal_observation (source_run_id, raw_signal_id, "
            "observed_at, created_at) VALUES (?,?,?,?)",
            ("sr-1", raw_id, TS, TS),
        )
    conn.execute("COMMIT")
    conn.close()


class CandidateCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai_cand_cli_")
        self.db = os.path.join(self.tmp, "test.db")
        self.output = pathlib.Path(self.tmp) / "out"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, extra, db=None, lane="emerging"):
        argv = [
            "candidate", "qualify-github",
            "--db-path", db if db is not None else self.db,
            "--week-key", WEEK,
            "--scope-key", SCOPE,
            "--lane", lane,
            "--limit", "50",
        ] + list(extra)
        buf = StringIO()
        rc = cli.main(argv, out=buf)
        return rc, buf.getvalue()

    def test_default_db_only_no_output_dirs(self):
        _seed(self.db, [_RESEARCH, _WATCH, _REJECT])
        rc, output = self._run([])
        self.assertEqual(rc, 0)
        for line in (
            "processed: 3",
            "candidates_created: 3",
            "discoveries_created: 3",
            "assessments_created: 3",
            "research: 1",
            "watch: 1",
            "rejected: 1",
            "markdown_written: 0",
        ):
            self.assertIn(line, output)
        self.assertFalse(self.output.exists())  # no output dirs created

    def test_emit_without_output_root_is_config_error_no_side_effects(self):
        fresh_db = os.path.join(self.tmp, "fresh1.db")
        rc, output = self._run(["--emit-candidate-markdown"], db=fresh_db)
        self.assertEqual(rc, 2)
        self.assertIn("--output-root is required", output)
        self.assertFalse(os.path.exists(fresh_db))

    def test_emit_without_allow_output_write_is_security_error(self):
        fresh_db = os.path.join(self.tmp, "fresh2.db")
        rc, output = self._run(
            ["--emit-candidate-markdown", "--output-root", str(self.output)],
            db=fresh_db,
        )
        self.assertEqual(rc, 4)
        self.assertIn("output write not allowed", output)
        self.assertFalse(os.path.exists(fresh_db))
        self.assertFalse(self.output.exists())

    def test_emit_writes_only_research_cards(self):
        _seed(self.db, [_RESEARCH, _WATCH, _REJECT])
        rc, output = self._run([
            "--emit-candidate-markdown",
            "--output-root", str(self.output),
            "--allow-output-write",
        ])
        self.assertEqual(rc, 0)
        self.assertIn("markdown_written: 1", output)
        research_dir = self.output / "Candidates" / "Research"
        self.assertEqual(len(list(research_dir.glob("*.md"))), 1)
        # WATCH and REJECT never render; Candidates/Watch is not created.
        self.assertFalse((self.output / "Candidates" / "Watch").exists())
        # No legacy material directories either.
        self.assertFalse((self.output / "Inbox").exists())

    def test_generated_markdown_satisfies_card_contract(self):
        _seed(self.db, [_RESEARCH])
        rc, _ = self._run([
            "--emit-candidate-markdown",
            "--output-root", str(self.output),
            "--allow-output-write",
        ])
        self.assertEqual(rc, 0)
        path = next(iter((self.output / "Candidates" / "Research").glob("*.md")))
        text = path.read_text(encoding="utf-8")
        self.assertIn("这是候选卡（调试/研究队列），不是可发布素材。", text)
        self.assertIn("## 6. 缺失证据", text)
        self.assertIn("- specific_event", text)
        body = text.split("---\n", 2)[2]
        self.assertIsNone(re.search(r"[0-9a-f]{64}", body))
        for forbidden in ("建议发布", "可以直接写", "READY_TO_WRITE", "NEEDS_TESTING"):
            self.assertNotIn(forbidden, text)

    def test_invalid_lane_returns_two(self):
        _seed(self.db, [_RESEARCH])
        rc, _ = self._run([], lane="hot")
        self.assertEqual(rc, 2)

    def test_invalid_limit_returns_two(self):
        _seed(self.db, [_RESEARCH])
        rc, _ = self._run(["--limit", "101"])
        self.assertEqual(rc, 2)

    def test_invalid_scope_returns_two(self):
        buf = StringIO()
        rc = cli.main(
            ["candidate", "qualify-github", "--db-path", self.db,
             "--week-key", WEEK, "--scope-key", "UPPER", "--lane", "emerging",
             "--limit", "10"],
            out=buf,
        )
        self.assertEqual(rc, 2)

    def test_output_has_no_leaks(self):
        _seed(self.db, [_RESEARCH])
        _, output = self._run([
            "--emit-candidate-markdown",
            "--output-root", str(self.output),
            "--allow-output-write",
        ])
        self.assertNotIn("example-org", output)
        self.assertNotIn("github.com", output)
        self.assertNotIn(SCOPE, output)
        self.assertNotIn(self.db, output)
        self.assertNotIn(str(self.output), output)

    def test_markdown_failure_keeps_committed_candidates(self):
        import ai_signal.outputs.candidate_markdown as cm

        _seed(self.db, [_RESEARCH])
        original = cm.publish_candidate_markdown

        def boom(root, qc):
            raise cm.CandidateMarkdownError("injected")

        cm.publish_candidate_markdown = boom
        try:
            rc, output = self._run([
                "--emit-candidate-markdown",
                "--output-root", str(self.output),
                "--allow-output-write",
            ])
        finally:
            cm.publish_candidate_markdown = original
        self.assertEqual(rc, 5)
        self.assertIn("output error", output)
        self.assertNotIn("database error", output)
        conn = S._open(self.db)
        try:
            count = conn.execute("SELECT COUNT(*) FROM candidate").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 1)
        self.assertIn("markdown_written: 0", output)

    def test_os_replace_permission_error_is_safe(self):
        # A real filesystem failure (os.replace -> PermissionError) must be
        # converted to CandidateMarkdownError, keep committed DB rows, and
        # never leak a path or OS error text into the CLI output.
        _seed(self.db, [_RESEARCH])
        real_replace = os.replace

        def boom(src, dst):
            raise PermissionError(13, "Permission denied")

        os.replace = boom
        try:
            rc, output = self._run([
                "--emit-candidate-markdown",
                "--output-root", str(self.output),
                "--allow-output-write",
            ])
        finally:
            os.replace = real_replace

        self.assertEqual(rc, 5)
        self.assertIn("output error", output)
        self.assertNotIn("database error", output)
        self.assertIn("markdown_written: 0", output)
        # No path or OS error detail may leak.
        self.assertNotIn(str(self.output), output)
        self.assertNotIn("Permission denied", output)
        self.assertNotIn("PermissionError", output)
        # Candidate/Discovery/Assessment were committed before the output phase.
        conn = S._open(self.db)
        try:
            for table in ("candidate", "candidate_discovery", "candidate_assessment"):
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0],
                    1,
                    table,
                )
        finally:
            conn.close()

    def test_rerun_creates_no_duplicates(self):
        _seed(self.db, [_RESEARCH])
        self._run([])
        rc, output = self._run([])
        self.assertEqual(rc, 0)
        self.assertIn("candidates_created: 0", output)
        self.assertIn("discoveries_existing: 1", output)
        self.assertIn("assessments_created: 0", output)


if __name__ == "__main__":
    unittest.main()
