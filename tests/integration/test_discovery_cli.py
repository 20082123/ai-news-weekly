"""CLI tests for ``ai-signal discover github`` (phase 2C2-D).

The runner itself is covered end-to-end with a fake transport at the
pipeline layer; here the CLI validates argument handling, exit codes, safe
output and a REAL-pipeline regression (the runner runs for real with an
injected fake transport - no prefabricated result object, no real network).
"""

import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.cli import main  # noqa: E402
from ai_signal.discovery.policy import (  # noqa: E402
    GitHubDiscoveryPolicy,
    GitHubDiscoveryProbe,
)
from ai_signal.discovery.run import GitHubDiscoveryRunResult  # noqa: E402
from ai_signal.sources.github_rest import HttpResponse  # noqa: E402
from ai_signal.storage import sqlite as S  # noqa: E402

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
EXIT_DB_ERROR = 3
EXIT_SECURITY = 4
EXIT_CAPABILITY = 5


class _PartialSearchTransport:
    """No-arg transport returning a page with one valid + one malformed item.

    Patched over ``ai_signal.discovery.run.UrllibTransport`` so the REAL
    runner performs the collection; the malformed item makes the batch
    ``partial``. No network is ever touched.
    """

    def __init__(self):
        self.calls = []

    def get(self, url, headers, timeout_seconds, max_response_bytes):
        self.calls.append(url)
        valid = {
            "id": 1,
            "full_name": "example-org/repo-1",
            "html_url": "https://github.com/example-org/repo-1",
            "description": (
                "An AI agent harness that wraps multiple models for real "
                "task automation with a substantive enough description."
            ),
            "topics": ["agent", "harness"],
            "language": "Python",
            "stargazers_count": 1,
            "forks_count": 0,
            "created_at": "2026-06-01T00:00:00+00:00",
            "updated_at": "2026-08-10T00:00:00+00:00",
            "pushed_at": "2026-08-10T00:00:00+00:00",
            "homepage": None,
            "fork": False,
            "archived": False,
            "disabled": False,
            "is_template": False,
        }
        malformed = {
            "id": 999,
            "full_name": "example-org/broken",
            "html_url": "https://github.com/example-org/broken",
            # updated_at missing -> GitHubSource rejects the item -> partial
        }
        body = json.dumps(
            {"total_count": 2, "items": [valid, malformed]}
        ).encode("utf-8")
        return HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=body,
            final_url="https://api.github.com/search/repositories",
        )


def _partial_policy():
    return GitHubDiscoveryPolicy(
        id="p-part-v1",
        lane="emerging",
        probes=(
            GitHubDiscoveryProbe(
                probe_id="p-part-v1-q1",
                kind="search",
                scope_key="ghp-part-v1-q1",
                spec={
                    "query": "topic:test-agent pushed:>2026-07-01",
                    "sort": "updated",
                    "order": "desc",
                    "per_page": 25,
                    "max_pages": 3,
                },
                priority=0,
            ),
        ),
        candidate_limit=50,
        research_budget=2,
    )


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

    def test_partial_status_requires_db_path(self):
        code, _ = self._run(["discover", "github", "--status"])
        self.assertEqual(code, EXIT_CONFIG_ERROR)

    def test_real_pipeline_partial_run_exits_capability(self):
        # Acceptance regression: a REAL pipeline run whose collection comes
        # back partial must exit capability code 5 - not a mocked result.
        import ai_signal.discovery.policy as policy_module

        with mock.patch.object(
            policy_module, "POLICY_CATALOG", {"p-part-v1": _partial_policy()}
        ), mock.patch(
            "ai_signal.discovery.run.UrllibTransport", _PartialSearchTransport
        ):
            code, text = self._run(
                ["discover", "github", "--policy", "p-part-v1",
                 "--db-path", self.db, "--week-key", "2026-W33",
                 "--allow-network"]
            )
        self.assertEqual(code, EXIT_CAPABILITY)
        self.assertIn("status: partial", text)
        self.assertIn("research: 1", text)  # the valid item still qualified
        # The probe run and the discovery run both recorded partial with the
        # stable, payload-free warning code.
        conn = S._open(self.db)
        try:
            probe = conn.execute(
                "SELECT status, warning_count, warnings "
                "FROM github_discovery_probe_run"
            ).fetchone()
            self.assertEqual(probe["status"], "partial")
            self.assertEqual(probe["warning_count"], 1)
            self.assertEqual(json.loads(probe["warnings"]), ["PROBE_PARTIAL"])
            run = conn.execute(
                "SELECT status FROM github_discovery_run"
            ).fetchone()
            self.assertEqual(run["status"], "partial")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
