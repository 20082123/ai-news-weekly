"""Integration tests for the ``collect github-live`` CLI command.

These tests inject a fake HTTP transport by patching ``cli.UrllibTransport``.
No real network call is ever made: the production transport is never used.
"""

import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from io import StringIO

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal import cli  # noqa: E402
from ai_signal.sources.github_rest import HttpResponse  # noqa: E402

QUERY = "topic:ai-agent pushed:>2026-08-01"
SCOPE = "ai-agents-v1"
_OK_FINAL = "https://api.github.com/search/repositories"


def _item(item_id):
    return {
        "id": item_id,
        "full_name": "example-org/example-%d" % item_id,
        "html_url": "https://example.com/example-org/example-%d" % item_id,
        "description": "an example repository",
        "language": "Python",
        "stargazers_count": 3,
        "forks_count": 0,
        "topics": ["example"],
        "pushed_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-02T00:00:00Z",
    }


class _FakeTransport:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = 0

    def get(self, url, headers, timeout_seconds, max_response_bytes):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.response


class _FakeTransportFactory:
    """Stand-in for ``UrllibTransport`` that returns a shared fake instance."""

    def __init__(self, fake):
        self._fake = fake

    def __call__(self):
        return self._fake


def _ok_body(items):
    return json.dumps({"total_count": len(items), "items": items}).encode("utf-8")


class CollectGithubLiveCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai_signal_2b1_cli_")
        self.db = os.path.join(self.tmp, "test.db")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _patch_transport(self, fake):
        original = cli.UrllibTransport
        cli.UrllibTransport = _FakeTransportFactory(fake)
        self.addCleanup(setattr, cli, "UrllibTransport", original)

    def _run(self, extra):
        argv = [
            "collect",
            "github-live",
            "--query",
            QUERY,
            "--scope-key",
            SCOPE,
            "--db-path",
            self.db,
            "--week-key",
            "2026-W33",
        ] + list(extra)
        buf = StringIO()
        rc = cli.main(argv, out=buf)
        return rc, buf.getvalue()

    def test_missing_allow_network_returns_four_and_creates_no_db(self):
        rc, output = self._run([])  # no --allow-network
        self.assertEqual(rc, 4)
        self.assertIn("network not allowed", output)
        self.assertFalse(os.path.exists(self.db), "database must not be created")

    def test_invalid_query_returns_two(self):
        rc, _ = self._run(["--query", "   ", "--allow-network"])
        self.assertEqual(rc, 2)

    def test_invalid_scope_returns_two(self):
        rc, _ = self._run(["--scope-key", "UpperCase", "--allow-network"])
        self.assertEqual(rc, 2)

    def test_invalid_timeout_returns_two(self):
        rc, _ = self._run(["--timeout", "99", "--allow-network"])
        self.assertEqual(rc, 2)

    def test_success_with_fake_transport_returns_zero(self):
        fake = _FakeTransport(
            response=HttpResponse(
                status=200,
                headers={"Content-Type": "application/json"},
                body=_ok_body([_item(1), _item(2)]),
                final_url=_OK_FINAL,
            )
        )
        self._patch_transport(fake)
        rc, output = self._run(["--allow-network"])
        self.assertEqual(rc, 0)
        self.assertIn("status: success", output)
        self.assertEqual(fake.calls, 1)  # proves the fake transport was used

    def test_rate_limited_with_fake_transport_returns_five(self):
        fake = _FakeTransport(
            response=HttpResponse(
                status=429,
                headers={"Content-Type": "application/json", "X-RateLimit-Remaining": "0"},
                body=b"{}",
                final_url=_OK_FINAL,
            )
        )
        self._patch_transport(fake)
        rc, output = self._run(["--allow-network"])
        self.assertEqual(rc, 5)
        self.assertEqual(fake.calls, 1)

    def test_output_contains_no_sensitive_data(self):
        fake = _FakeTransport(
            response=HttpResponse(
                status=200,
                headers={"Content-Type": "application/json"},
                body=_ok_body([_item(1)]),
                final_url=_OK_FINAL,
            )
        )
        self._patch_transport(fake)
        _, output = self._run(["--allow-network"])
        self.assertNotIn(QUERY, output)
        self.assertNotIn(SCOPE, output)
        self.assertNotIn("api.github.com", output)
        self.assertNotIn("example.com", output)
        self.assertNotIn(self.db, output)
        self.assertNotIn("scope", output)


if __name__ == "__main__":
    unittest.main()
