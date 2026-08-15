"""Integration tests for :mod:`ai_signal.discovery.run` (phase 2C2-B).

All tests are offline: a fake transport answers the GitHub Search endpoint, so
no test ever reaches the network. ``allow_network=True`` is passed to the
runner exactly as the CLI will do after its own gate; the runner's gate
(default False) is tested separately by asserting the database is never
created.
"""

import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

import ai_signal.discovery.policy as policy_module  # noqa: E402
from ai_signal.discovery.policy import (  # noqa: E402
    GitHubDiscoveryPolicy,
    GitHubDiscoveryProbe,
)
from ai_signal.discovery.run import run_github_discovery  # noqa: E402
from ai_signal.domain.models import GitHubScopeBinding  # noqa: E402
from ai_signal.pipeline.collect import CollectionPolicyError  # noqa: E402
from ai_signal.sources.github_rest import HttpResponse  # noqa: E402
from ai_signal.storage import sqlite as S  # noqa: E402
from ai_signal.storage.discovery_repositories import (  # noqa: E402
    GitHubScopeBindingRepository,
)

CLOCK = "2026-08-15T08:00:00+00:00"


def _utc(text):
    return datetime.fromisoformat(text)


def _repo_item(repo_id, *, fork=False, description=None):
    return {
        "id": repo_id,
        "full_name": "example-org/repo-%d" % repo_id,
        "html_url": "https://github.com/example-org/repo-%d" % repo_id,
        "description": description or (
            "An AI agent harness that wraps multiple models for real task "
            "automation with a substantive enough description for the gate."
        ),
        "topics": ["agent", "harness"],
        "language": "Python",
        "stargazers_count": 1,
        "forks_count": 0,
        "created_at": "2026-06-01T00:00:00+00:00",
        "updated_at": "2026-08-10T00:00:00+00:00",
        "pushed_at": "2026-08-10T00:00:00+00:00",
        "homepage": None,
        "fork": fork,
        "archived": False,
        "disabled": False,
        "is_template": False,
    }


def _search_probe(probe_id, scope_key, query, priority):
    return GitHubDiscoveryProbe(
        probe_id=probe_id,
        kind="search",
        scope_key=scope_key,
        spec={
            "query": query,
            "sort": "updated",
            "order": "desc",
            "per_page": 25,
            "max_pages": 3,
        },
        priority=priority,
    )


def _policy(policy_id, probes, lane="emerging", limit=50, budget=2):
    return GitHubDiscoveryPolicy(
        id=policy_id,
        lane=lane,
        probes=tuple(probes),
        candidate_limit=limit,
        research_budget=budget,
    )


class _FakeTransport:
    """Answers the Search API per query string; records every call."""

    def __init__(self, pages_by_query, failure_queries=()):
        self.pages_by_query = pages_by_query
        self.failure_queries = set(failure_queries)
        self.calls = []

    def get(self, url, headers, timeout_seconds, max_response_bytes):
        self.calls.append(url)
        query = parse_qs(urlparse(url).query).get("q", [""])[0]
        if query in self.failure_queries:
            raise RuntimeError("simulated transport failure")
        items = self.pages_by_query.get(query)
        if items is None:
            raise RuntimeError("unexpected query")
        body = json.dumps({"total_count": len(items), "items": items}).encode("utf-8")
        return HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=body,
            final_url="https://api.github.com/search/repositories",
        )


class DiscoveryPipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai_disc_run_")
        self.db = os.path.join(self.tmp, "test.db")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _patch_catalog(self, policy):
        patcher = mock.patch.object(
            policy_module, "POLICY_CATALOG", {policy.id: policy}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return policy

    def _bind_scope(self, scope_key, spec_hash):
        S.initialize_database(self.db)
        conn = S._open(self.db)
        conn.execute("BEGIN")
        GitHubScopeBindingRepository(conn).insert_or_get(
            GitHubScopeBinding(
                scope_key=scope_key,
                probe_id="other-probe",
                policy_id="other-policy",
                spec_hash=spec_hash,
                created_at=_utc(CLOCK),
            )
        )
        conn.execute("COMMIT")
        conn.close()

    def _seed_legacy_cursor(self, scope_key):
        S.initialize_database(self.db)
        conn = S._open(self.db)
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO collection_run (id, week_key, started_at, status, "
            "config_snapshot, created_at) VALUES (?,?,?,?,?,?)",
            ("cr-legacy", "2026-W32", CLOCK, "success", "{}", CLOCK),
        )
        conn.execute(
            "INSERT INTO source_cursor (source, scope_key, cursor, source_version, "
            "last_run_id, updated_at) VALUES (?,?,?,?,?,?)",
            ("github", scope_key, "page:2", "github-rest-v1", "cr-legacy", CLOCK),
        )
        conn.execute("COMMIT")
        conn.close()

    # ------------------------------------------------------------------ #
    def test_full_run_budget_and_selection(self):
        query = "topic:test-agent pushed:>2026-07-01"
        transport = _FakeTransport(
            {
                query: [
                    _repo_item(1),
                    _repo_item(2),
                    _repo_item(3),
                    _repo_item(4, fork=True),  # junk -> reject
                ]
            }
        )
        policy = self._patch_catalog(
            _policy(
                "test-v1",
                [_search_probe("test-v1-q1", "ghp-test-v1-q1", query, 0)],
                budget=2,
            )
        )
        result = run_github_discovery(
            self.db,
            "2026-W33",
            "test-v1",
            allow_network=True,
            transport_factory=lambda: transport,
            clock=lambda: _utc(CLOCK),
        )
        self.assertEqual(result.status, "success")
        self.assertEqual(result.probes_total, 1)
        self.assertEqual(result.processed, 4)
        self.assertEqual(result.research, 3)
        self.assertEqual(result.rejected, 1)
        self.assertEqual(result.selections_total, 3)
        self.assertEqual(result.queued, 2)
        self.assertEqual(result.over_budget, 1)
        self.assertEqual(len(transport.calls), 1)

        conn = S._open(self.db)
        try:
            rows = conn.execute(
                "SELECT queue_state, qualification_decision, budget_reason "
                "FROM github_candidate_selection ORDER BY selection_rank"
            ).fetchall()
            self.assertEqual([row["queue_state"] for row in rows],
                             ["queued", "queued", "over_budget"])
            # The budget must NEVER downgrade the qualification decision.
            self.assertTrue(all(row["qualification_decision"] == "research"
                                for row in rows))
            self.assertEqual(rows[2]["budget_reason"], "RESEARCH_BUDGET_EXCEEDED")
            # Binding claimed for the scope with the probe's spec hash.
            binding = GitHubScopeBindingRepository(conn).get("ghp-test-v1-q1")
            probe = policy.probes[0]
            self.assertEqual(binding.spec_hash, probe.spec_hash)
        finally:
            conn.close()

    def test_spec_mismatch_blocks_before_network(self):
        query = "topic:test-agent pushed:>2026-07-01"
        transport = _FakeTransport({query: [_repo_item(1)]})
        policy = self._patch_catalog(
            _policy(
                "test-v1",
                [_search_probe("test-v1-q1", "ghp-test-v1-q1", query, 0)],
            )
        )
        self._bind_scope("ghp-test-v1-q1", "f" * 64)
        result = run_github_discovery(
            self.db,
            "2026-W33",
            "test-v1",
            allow_network=True,
            transport_factory=lambda: transport,
            clock=lambda: _utc(CLOCK),
        )
        # No request was ever made.
        self.assertEqual(transport.calls, [])
        self.assertEqual(result.probes_blocked, 1)
        self.assertIn("SCOPE_SPEC_MISMATCH", result.warnings)
        self.assertEqual(result.status, "failed")  # every probe blocked
        conn = S._open(self.db)
        try:
            row = conn.execute(
                "SELECT status, warning_count, warnings "
                "FROM github_discovery_probe_run"
            ).fetchone()
            self.assertEqual(row["status"], "blocked")
            self.assertEqual(row["warning_count"], 1)
            self.assertEqual(json.loads(row["warnings"]),
                             ["SCOPE_SPEC_MISMATCH"])
        finally:
            conn.close()

    def test_unbound_legacy_cursor_blocks(self):
        query = "topic:test-agent pushed:>2026-07-01"
        transport = _FakeTransport({query: [_repo_item(1)]})
        policy = self._patch_catalog(
            _policy(
                "test-v1",
                [_search_probe("test-v1-q1", "ghp-test-v1-q1", query, 0)],
            )
        )
        self._seed_legacy_cursor("ghp-test-v1-q1")
        result = run_github_discovery(
            self.db,
            "2026-W33",
            "test-v1",
            allow_network=True,
            transport_factory=lambda: transport,
            clock=lambda: _utc(CLOCK),
        )
        self.assertEqual(transport.calls, [])
        self.assertEqual(result.probes_blocked, 1)
        self.assertIn("SCOPE_UNBOUND_CURSOR", result.warnings)

    def test_dedup_across_probes_single_selection(self):
        q1 = "topic:test-agent pushed:>2026-07-01"
        q2 = "test-agent pushed:>2026-07-01"
        transport = _FakeTransport({q1: [_repo_item(1)], q2: [_repo_item(1)]})
        policy = self._patch_catalog(
            _policy(
                "test-v1",
                [
                    _search_probe("test-v1-q1", "ghp-test-v1-q1", q1, 0),
                    _search_probe("test-v1-q2", "ghp-test-v1-q2", q2, 1),
                ],
                budget=5,
            )
        )
        result = run_github_discovery(
            self.db,
            "2026-W33",
            "test-v1",
            allow_network=True,
            transport_factory=lambda: transport,
            clock=lambda: _utc(CLOCK),
        )
        self.assertEqual(result.status, "success")
        self.assertEqual(result.selections_total, 1)
        conn = S._open(self.db)
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM candidate").fetchone()[0], 1
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM candidate_discovery"
                ).fetchone()[0],
                2,
            )
            sel = conn.execute(
                "SELECT winning_discovery_id FROM github_candidate_selection"
            ).fetchone()
            disc = conn.execute(
                "SELECT scope_key FROM candidate_discovery WHERE id = ?",
                (sel["winning_discovery_id"],),
            ).fetchone()
            # The higher-priority probe (q1) wins the discovery.
            self.assertEqual(disc["scope_key"], "ghp-test-v1-q1")
        finally:
            conn.close()

    def test_probe_failure_isolation(self):
        q1 = "topic:test-agent pushed:>2026-07-01"
        q2 = "test-agent pushed:>2026-07-01"
        transport = _FakeTransport(
            {q1: [_repo_item(1)], q2: []}, failure_queries=(q2,)
        )
        policy = self._patch_catalog(
            _policy(
                "test-v1",
                [
                    _search_probe("test-v1-q1", "ghp-test-v1-q1", q1, 0),
                    _search_probe("test-v1-q2", "ghp-test-v1-q2", q2, 1),
                ],
                budget=5,
            )
        )
        result = run_github_discovery(
            self.db,
            "2026-W33",
            "test-v1",
            allow_network=True,
            transport_factory=lambda: transport,
            clock=lambda: _utc(CLOCK),
        )
        # One probe failed; the run degraded but the other probe's candidate
        # was still qualified and selected - a source failure must not
        # silently vanish and must not abort the run.
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.probes_failed, 1)
        self.assertEqual(result.selections_total, 1)
        self.assertIn("PROBE_FAILED", result.warnings)
        conn = S._open(self.db)
        try:
            probe = conn.execute(
                "SELECT status, warnings FROM github_discovery_probe_run "
                "WHERE probe_id = 'test-v1-q2'"
            ).fetchone()
            self.assertEqual(probe["status"], "failed")
            self.assertEqual(json.loads(probe["warnings"]), ["PROBE_FAILED"])
        finally:
            conn.close()

    def test_rerun_creates_new_run_but_no_duplicates(self):
        query = "topic:test-agent pushed:>2026-07-01"
        transport = _FakeTransport({query: [_repo_item(1)]})
        policy = self._patch_catalog(
            _policy(
                "test-v1",
                [_search_probe("test-v1-q1", "ghp-test-v1-q1", query, 0)],
                budget=5,
            )
        )
        first = run_github_discovery(
            self.db,
            "2026-W33",
            "test-v1",
            allow_network=True,
            transport_factory=lambda: transport,
            clock=lambda: _utc(CLOCK),
        )
        second = run_github_discovery(
            self.db,
            "2026-W33",
            "test-v1",
            allow_network=True,
            transport_factory=lambda: transport,
            clock=lambda: _utc(CLOCK),
        )
        self.assertEqual(first.status, "success")
        self.assertEqual(second.status, "success")
        self.assertNotEqual(first.run_id, second.run_id)
        conn = S._open(self.db)
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM candidate").fetchone()[0], 1
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM github_discovery_run"
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM github_candidate_selection"
                ).fetchone()[0],
                2,  # one final selection per run
            )
        finally:
            conn.close()

    def test_no_network_gate_refuses_before_db(self):
        query = "topic:test-agent pushed:>2026-07-01"
        self._patch_catalog(
            _policy(
                "test-v1",
                [_search_probe("test-v1-q1", "ghp-test-v1-q1", query, 0)],
            )
        )
        with self.assertRaises(CollectionPolicyError):
            run_github_discovery(self.db, "2026-W33", "test-v1")
        # Refused before any database work: no file, no side effects.
        self.assertFalse(os.path.exists(self.db))

    def test_zero_probe_policy_rejected(self):
        self._patch_catalog(_policy("test-v1", []))
        with self.assertRaises(Exception):
            run_github_discovery(self.db, "2026-W33", "test-v1",
                                 allow_network=True)

    def test_real_emerging_catalog_end_to_end(self):
        # Drive the REAL emerging-v1 catalog with a fake transport keyed by
        # its actual query strings; proves the catalog is runnable as-is.
        policy = policy_module.get_policy("emerging-v1")
        pages = {}
        for probe in policy.probes:
            pages[probe.spec["query"]] = [_repo_item(70000)]
        transport = _FakeTransport(pages)
        result = run_github_discovery(
            self.db,
            "2026-W33",
            "emerging-v1",
            allow_network=True,
            transport_factory=lambda: transport,
            clock=lambda: _utc(CLOCK),
        )
        self.assertEqual(result.status, "success")
        self.assertEqual(result.probes_total, 3)
        self.assertEqual(result.research, 3)
        # Same repo id per probe -> one candidate, one selection.
        conn = S._open(self.db)
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM candidate").fetchone()[0], 1
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM github_candidate_selection"
                ).fetchone()[0],
                1,
            )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
