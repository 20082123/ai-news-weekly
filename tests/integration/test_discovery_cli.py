"""CLI tests for ``ai-signal discover github`` (phase 2C2-D).

The runner itself is covered end-to-end with a fake transport at the
pipeline layer; here the CLI validates argument handling, exit codes, safe
output and the mock-level happy path (no real network ever).
"""

import io
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.cli import main  # noqa: E402
from ai_signal.discovery.run import GitHubDiscoveryRunResult  # noqa: E402

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
EXIT_DB_ERROR = 3
EXIT_SECURITY = 4
EXIT_CAPABILITY = 5


class DiscoverCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai_disc_cli_")
        self.db = os.path.join(self.tmp, "test.db")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, argv):
        out = io.StringIO()
        code = main(argv, out)
        return code, out.getvalue()

    def test_list_policies_safe_output(self):
        code, text = self._run(["discover", "github", "--list-policies"])
        self.assertEqual(code, EXIT_OK)
        lowered = text.lower()
        for policy in ("watchlist-v1", "mature-v1", "emerging-v1", "ecosystem-v1"):
            self.assertIn(policy, text)
        # Never a query, scope key or hash.
        for forbidden in ("query", "agent", "mcp", "ghp-", "pushed"):
            self.assertNotIn(forbidden, lowered)

    def test_run_requires_db_path(self):
        code, text = self._run(
            ["discover", "github", "--policy", "emerging-v1",
             "--week-key", "2026-W33"]
        )
        self.assertEqual(code, EXIT_CONFIG_ERROR)
        self.assertIn("--db-path", text)

    def test_invalid_week_key(self):
        code, _ = self._run(
            ["discover", "github", "--policy", "emerging-v1",
             "--db-path", self.db, "--week-key", "nope"]
        )
        self.assertEqual(code, EXIT_CONFIG_ERROR)

    def test_unknown_policy(self):
        code, text = self._run(
            ["discover", "github", "--policy", "nope-v1",
             "--db-path", self.db, "--week-key", "2026-W33",
             "--allow-network"]
        )
        self.assertEqual(code, EXIT_CONFIG_ERROR)
        self.assertIn("config error", text)

    def test_network_gate_refused_before_db(self):
        code, text = self._run(
            ["discover", "github", "--policy", "emerging-v1",
             "--db-path", self.db, "--week-key", "2026-W33"]
        )
        self.assertEqual(code, EXIT_SECURITY)
        self.assertIn("policy error", text)
        self.assertFalse(os.path.exists(self.db))

    def test_zero_probe_policy_rejected(self):
        from ai_signal.discovery.policy import GitHubDiscoveryPolicy

        empty = GitHubDiscoveryPolicy(
            id="empty-v1",
            lane="emerging",
            probes=(),
            candidate_limit=10,
            research_budget=2,
        )
        with mock.patch(
            "ai_signal.discovery.policy.POLICY_CATALOG", {"empty-v1": empty}
        ):
            code, text = self._run(
                ["discover", "github", "--policy", "empty-v1",
                 "--db-path", self.db, "--week-key", "2026-W33",
                 "--allow-network"]
            )
        self.assertEqual(code, EXIT_CONFIG_ERROR)
        self.assertIn("config error", text)
        # Refused before any database work.
        self.assertFalse(os.path.exists(self.db))

    def test_happy_path_mocked_runner(self):
        result = GitHubDiscoveryRunResult(
            run_id="run-1",
            policy_id="emerging-v1",
            lane="emerging",
            week_key="2026-W33",
            status="success",
            probes_total=3,
            probes_blocked=0,
            probes_failed=0,
            processed=4,
            research=3,
            watch=0,
            rejected=1,
            quarantined=0,
            selections_total=3,
            queued=2,
            over_budget=1,
            beyond_candidate_limit=0,
        )
        # Patch BOTH namespaces (package re-export and the defining module)
        # so no future import-style change can ever slip into a real run.
        with mock.patch(
            "ai_signal.discovery.run_github_discovery", return_value=result
        ) as package_mock, mock.patch(
            "ai_signal.discovery.run.run_github_discovery", return_value=result
        ) as module_mock:
            code, text = self._run(
                ["discover", "github", "--policy", "emerging-v1",
                 "--db-path", self.db, "--week-key", "2026-W33",
                 "--allow-network"]
            )
        call = (package_mock if package_mock.called else module_mock).call_args
        self.assertIsNotNone(call)
        self.assertEqual(code, EXIT_OK)
        self.assertIn("status: success", text)
        self.assertIn("queued: 2", text)
        self.assertIn("over_budget: 1", text)
        # The CLI output carries counts only - no query/scope/hash/path.
        lowered = text.lower()
        for forbidden in ("query", "agent", "ghp-"):
            self.assertNotIn(forbidden, lowered)
        self.assertEqual(call.args[1], "2026-W33")
        self.assertEqual(call.args[2], "emerging-v1")
        self.assertTrue(call.kwargs["allow_network"])

    def test_partial_status_exits_capability(self):
        result = GitHubDiscoveryRunResult(
            run_id="run-1",
            policy_id="emerging-v1",
            lane="emerging",
            week_key="2026-W33",
            status="partial",
            probes_total=3,
            probes_blocked=1,
            probes_failed=0,
            processed=0,
            research=0,
            watch=0,
            rejected=0,
            quarantined=0,
            selections_total=0,
            queued=0,
            over_budget=0,
            beyond_candidate_limit=0,
            warnings=("SCOPE_SPEC_MISMATCH",),
        )
        with mock.patch(
            "ai_signal.discovery.run_github_discovery", return_value=result
        ), mock.patch(
            "ai_signal.discovery.run.run_github_discovery", return_value=result
        ):
            code, text = self._run(
                ["discover", "github", "--policy", "emerging-v1",
                 "--db-path", self.db, "--week-key", "2026-W33",
                 "--allow-network"]
            )
        self.assertEqual(code, EXIT_CAPABILITY)
        self.assertIn("status: partial", text)

    def test_status_on_missing_db_reports_database_error(self):
        code, text = self._run(
            ["discover", "github", "--status", "--db-path", self.db]
        )
        self.assertEqual(code, EXIT_DB_ERROR)
        self.assertIn("database does not exist", text)
        # A read-only status must never create a database file.
        self.assertFalse(os.path.exists(self.db))

    def test_status_requires_db_path(self):
        code, _ = self._run(["discover", "github", "--status"])
        self.assertEqual(code, EXIT_CONFIG_ERROR)


if __name__ == "__main__":
    unittest.main()
