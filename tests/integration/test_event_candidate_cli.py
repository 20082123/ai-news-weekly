"""CLI tests for ``ai-signal event-candidate`` (phase 2C3)."""

import io
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.cli import main  # noqa: E402

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
EXIT_DB_ERROR = 3


def _add_args(db, **overrides):
    args = [
        "event-candidate", "add", "--db-path", db,
        "--signal-type", "economics_access",
        "--subject", "DeepSeek V4 API",
        "--change-summary", "峰谷计价生效",
        "--audience", "API 开发者",
        "--impact", "成本重算",
        "--priority", "90",
    ]
    for key, value in overrides.items():
        args.extend([key, str(value)])
    return args


class EventCandidateCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai_evc_cli_")
        self.db = os.path.join(self.tmp, "test.db")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, argv):
        out = io.StringIO()
        code = main(argv, out)
        return code, out.getvalue()

    def test_add_and_list_roundtrip(self):
        code, text = self._run(_add_args(
            self.db,
            **{"--ref": "official_announcement_candidate:deepseek-news260813:DeepSeek V4-Pro GA 公告"}
        ))
        self.assertEqual(code, EXIT_OK)
        self.assertIn("refs: 1", text)
        code, text = self._run(["event-candidate", "list", "--db-path", self.db])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("type=economics_access", text)
        self.assertIn("priority=90", text)
        self.assertIn("subject=DeepSeek V4 API", text)

    def test_add_idempotent(self):
        first, _ = self._run(_add_args(self.db))
        second, text = self._run(_add_args(self.db))
        self.assertEqual(first, EXIT_OK)
        self.assertEqual(second, EXIT_OK)
        self.assertIn("id:", text)
        code, text = self._run(["event-candidate", "list", "--db-path", self.db])
        self.assertEqual(text.count("subject=DeepSeek V4 API"), 1)

    def test_invalid_signal_type_rejected(self):
        code, text = self._run(_add_args(self.db, **{"--signal-type": "nope"}))
        self.assertEqual(code, EXIT_CONFIG_ERROR)
        self.assertIn("invalid signal type", text)

    def test_invalid_priority_rejected(self):
        code, text = self._run(_add_args(self.db, **{"--priority": "101"}))
        self.assertEqual(code, EXIT_CONFIG_ERROR)
        self.assertIn("priority", text)

    def test_invalid_ref_rejected(self):
        code, text = self._run(_add_args(self.db, **{"--ref": "broken"}))
        self.assertEqual(code, EXIT_CONFIG_ERROR)
        self.assertIn("--ref", text)

    def test_list_on_missing_db(self):
        code, text = self._run(["event-candidate", "list", "--db-path", self.db])
        self.assertEqual(code, EXIT_DB_ERROR)
        self.assertIn("database does not exist", text)

    def _seed_queued_chain(self, db, week="2026-W33"):
        from ai_signal.storage import sqlite as S

        S.initialize_database(db)
        ts = "2026-08-15T08:00:00+00:00"
        conn = S._open(db)
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO github_discovery_run (id, policy_id, policy_hash, "
            "week_key, lane, candidate_limit, research_budget, status, "
            "started_at, finished_at, warnings, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("gdr-0", "test-v1", "a" * 64, week, "emerging", 50, 5, "success",
             ts, ts, "[]", ts),
        )
        conn.execute(
            "INSERT INTO collection_run (id, week_key, started_at, status, "
            "config_snapshot, created_at) VALUES (?,?,?,?,?,?)",
            ("cr-0", week, ts, "success", "{}", ts),
        )
        conn.execute(
            "INSERT INTO raw_signal (id, collection_run_id, source, external_id, "
            "payload, payload_sha256, source_version, collected_at, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("raw-0", "cr-0", "github", "90000", "{}", "e" * 64,
             "github-rest-v1", ts, ts),
        )
        conn.execute(
            "INSERT INTO candidate (id, source, canonical_key, title, url, "
            "first_seen_at, last_seen_at, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("cand-0", "github", "github:repository:90000",
             "example-org/repo-0", "https://github.com/example-org/repo-0",
             ts, ts, ts, ts),
        )
        conn.execute(
            "INSERT INTO candidate_discovery (id, candidate_id, week_key, "
            "scope_key, lane, raw_signal_id, observed_at, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("disc-0", "cand-0", week, "ghp-t-v1-q1", "emerging", "raw-0", ts, ts),
        )
        conn.execute(
            "INSERT INTO candidate_assessment (id, candidate_discovery_id, "
            "policy_version, input_hash, decision, trigger_kind, trigger_summary, "
            "reason_codes, missing_evidence, attributes, assessed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("assess-0", "disc-0", "candidate-gate-v2", "f" * 64, "research",
             "repository_snapshot", "summary", "[]", "[]", "{}", ts),
        )
        conn.execute(
            "INSERT INTO github_candidate_selection (id, discovery_run_id, "
            "candidate_id, winning_discovery_id, winning_assessment_id, "
            "selection_rank, qualification_decision, queue_state, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("sel-0", "gdr-0", "cand-0", "disc-0", "assess-0", 0, "research",
             "queued", ts),
        )
        conn.execute("COMMIT")
        conn.close()

    def test_promote_invalid_week(self):
        code, text = self._run(
            ["event-candidate", "promote-github", "--db-path", self.db,
             "--week-key", "nope"]
        )
        self.assertEqual(code, EXIT_CONFIG_ERROR)
        self.assertIn("invalid week key", text)

    def test_promote_on_empty_db(self):
        code, text = self._run(
            ["event-candidate", "promote-github", "--db-path", self.db,
             "--week-key", "2026-W33"]
        )
        self.assertEqual(code, EXIT_OK)
        self.assertIn("processed: 0", text)
        self.assertIn("promoted: 0", text)

    def test_promote_happy_path_and_idempotent(self):
        self._seed_queued_chain(self.db)
        code, text = self._run(
            ["event-candidate", "promote-github", "--db-path", self.db,
             "--week-key", "2026-W33"]
        )
        self.assertEqual(code, EXIT_OK)
        self.assertIn("promoted: 1", text)
        self.assertIn("refs_added: 1", text)
        # Idempotent rerun skips the referenced candidate.
        code, text = self._run(
            ["event-candidate", "promote-github", "--db-path", self.db,
             "--week-key", "2026-W33"]
        )
        self.assertEqual(code, EXIT_OK)
        self.assertIn("promoted: 0", text)
        self.assertIn("already_promoted: 1", text)


if __name__ == "__main__":
    unittest.main()
