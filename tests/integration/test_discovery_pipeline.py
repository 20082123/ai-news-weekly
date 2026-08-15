"""Integration tests for :mod:`ai_signal.discovery.run` (phase 2C2-B).

All tests are offline: a fake transport answers the GitHub Search endpoint, so
no test ever reaches the network. ``allow_network=True`` is passed to the
runner exactly as the CLI will do after its own gate; the runner's gate
(default False) is tested separately by asserting the database is never
created.
"""

import hashlib
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
            # Acceptance: an idempotent rerun adds NO duplicate assessment
            # revision (same discovery, same input identity).
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM candidate_assessment"
                ).fetchone()[0],
                1,
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

    # ------------------------------------------------------------------ #
    # Acceptance fixes: partial propagation, scope ownership, priority.
    # ------------------------------------------------------------------ #
    def test_partial_probe_marks_probe_and_run_partial(self):
        q1 = "topic:test-agent pushed:>2026-07-01"
        q2 = "test-agent pushed:>2026-07-01"
        malformed = {
            "id": 999,
            "full_name": "example-org/broken",
            "html_url": "https://github.com/example-org/broken",
            # updated_at missing -> GitHubSource rejects the item -> partial
        }
        transport = _FakeTransport(
            {q1: [_repo_item(1), malformed], q2: [_repo_item(2)]}
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
        # One probe partial + one success -> run MUST be partial, not success.
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.probes_failed, 0)
        self.assertIn("PROBE_PARTIAL", result.warnings)
        conn = S._open(self.db)
        try:
            rows = {
                row["probe_id"]: (row["status"], json.loads(row["warnings"]))
                for row in conn.execute(
                    "SELECT probe_id, status, warnings "
                    "FROM github_discovery_probe_run"
                )
            }
            self.assertEqual(rows["test-v1-q1"][0], "partial")
            self.assertEqual(rows["test-v1-q1"][1], ["PROBE_PARTIAL"])
            self.assertEqual(rows["test-v1-q2"][0], "success")
            self.assertEqual(rows["test-v1-q2"][1], [])
        finally:
            conn.close()

    def test_all_partial_probes_still_partial_not_failed(self):
        query = "topic:test-agent pushed:>2026-07-01"
        malformed = {"id": 999, "full_name": "example-org/broken",
                     "html_url": "https://github.com/example-org/broken"}
        transport = _FakeTransport({query: [_repo_item(1), malformed]})
        self._patch_catalog(
            _policy(
                "test-v1",
                [_search_probe("test-v1-q1", "ghp-test-v1-q1", query, 0)],
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
        # Every probe partial: NOT failed (nothing failed), just degraded.
        self.assertEqual(result.status, "partial")

    def test_owner_mismatch_blocks_cross_policy(self):
        query = "topic:test-agent pushed:>2026-07-01"
        scope = "ghp-owner-shared-v1"

        self._patch_catalog(
            _policy(
                "owner-a-v1",
                [_search_probe("owner-a-q1", scope, query, 0)],
            )
        )
        transport_a = _FakeTransport({query: [_repo_item(1)]})
        first = run_github_discovery(
            self.db,
            "2026-W33",
            "owner-a-v1",
            allow_network=True,
            transport_factory=lambda: transport_a,
            clock=lambda: _utc(CLOCK),
        )
        self.assertEqual(first.status, "success")
        self.assertEqual(len(transport_a.calls), 1)

        # Snapshot the claimed binding and cursor BEFORE the second policy.
        conn = S._open(self.db)
        try:
            binding_before = conn.execute(
                "SELECT probe_id, policy_id, spec_hash, updated_at "
                "FROM github_discovery_scope_binding WHERE scope_key = ?",
                (scope,),
            ).fetchone()
            cursor_before = conn.execute(
                "SELECT cursor, updated_at FROM source_cursor "
                "WHERE source = 'github' AND scope_key = ?",
                (scope,),
            ).fetchone()
        finally:
            conn.close()

        # Second policy: SAME scope_key and SAME spec (same query -> same
        # spec_hash), but a different policy_id/probe_id. Must be blocked
        # before any network request.
        self._patch_catalog(
            _policy(
                "owner-b-v1",
                [_search_probe("owner-b-q1", scope, query, 0)],
            )
        )
        transport_b = _FakeTransport({query: [_repo_item(1)]})
        second = run_github_discovery(
            self.db,
            "2026-W33",
            "owner-b-v1",
            allow_network=True,
            transport_factory=lambda: transport_b,
            clock=lambda: _utc(CLOCK),
        )
        self.assertEqual(second.probes_blocked, 1)
        self.assertIn("SCOPE_BINDING_OWNER_MISMATCH", second.warnings)
        self.assertEqual(transport_b.calls, [])  # zero network requests

        conn = S._open(self.db)
        try:
            row = conn.execute(
                "SELECT status, warning_count, warnings "
                "FROM github_discovery_probe_run "
                "WHERE probe_id = 'owner-b-q1'"
            ).fetchone()
            self.assertEqual(row["status"], "blocked")
            self.assertEqual(json.loads(row["warnings"]),
                             ["SCOPE_BINDING_OWNER_MISMATCH"])
            # The original binding and cursor are untouched.
            binding_after = conn.execute(
                "SELECT probe_id, policy_id, spec_hash, updated_at "
                "FROM github_discovery_scope_binding WHERE scope_key = ?",
                (scope,),
            ).fetchone()
            cursor_after = conn.execute(
                "SELECT cursor, updated_at FROM source_cursor "
                "WHERE source = 'github' AND scope_key = ?",
                (scope,),
            ).fetchone()
            self.assertEqual(dict(binding_after), dict(binding_before))
            self.assertEqual(dict(cursor_after), dict(cursor_before))
            self.assertEqual(binding_after["probe_id"], "owner-a-q1")
            self.assertEqual(binding_after["policy_id"], "owner-a-v1")
        finally:
            conn.close()

    def test_reversed_probes_lowest_priority_wins(self):
        q_lo = "topic:test-agent pushed:>2026-07-01"   # priority 1
        q_hi = "test-agent pushed:>2026-07-01"         # priority 2, FIRST in tuple
        transport = _FakeTransport({q_lo: [_repo_item(1)], q_hi: [_repo_item(1)]})
        self._patch_catalog(
            _policy(
                "test-v1",
                [
                    _search_probe("test-v1-qhi", "ghp-rev-hi", q_hi, 2),
                    _search_probe("test-v1-qlo", "ghp-rev-lo", q_lo, 1),
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
            sel = conn.execute(
                "SELECT winning_discovery_id FROM github_candidate_selection"
            ).fetchone()
            disc = conn.execute(
                "SELECT scope_key FROM candidate_discovery WHERE id = ?",
                (sel["winning_discovery_id"],),
            ).fetchone()
            # Numerically smallest priority wins, NOT tuple order.
            self.assertEqual(disc["scope_key"], "ghp-rev-lo")
        finally:
            conn.close()

    # ------------------------------------------------------------------ #
    # Acceptance fixes: assessment audit identity (relation + policy context).
    # ------------------------------------------------------------------ #
    def _eco_rev_policy(self, targets, candidate_limit=50):
        return GitHubDiscoveryPolicy(
            id="eco-rev-v1",
            lane="ecosystem",
            probes=(
                _search_probe(
                    "eco-rev-q1",
                    "ghp-eco-rev-q1",
                    "wrapper in:name,description pushed:>2026-07-01",
                    0,
                ),
            ),
            candidate_limit=candidate_limit,
            research_budget=5,
            ecosystem_targets=targets,
        )

    def test_assessment_revisions_on_relation_and_context_change(self):
        from ai_signal.discovery.relation import EcosystemTargetSpec

        query = "wrapper in:name,description pushed:>2026-07-01"
        repo = _repo_item(
            1,
            description=(
                "A deployment wrapper around core and widget that manages "
                "model routing for agent teams with enough substance."
            ),
        )
        transport = _FakeTransport({query: [repo]})

        def run():
            return run_github_discovery(
                self.db,
                "2026-W33",
                "eco-rev-v1",
                allow_network=True,
                transport_factory=lambda: transport,
                clock=lambda: _utc(CLOCK),
            )

        # Run 1: relation matches "core".
        self._patch_catalog(
            self._eco_rev_policy(
                (EcosystemTargetSpec(target="example-org/core", aliases=("core",)),)
            )
        )
        first = run()
        self.assertEqual(first.status, "success")
        self.assertEqual(first.research, 1)

        # Run 2: same policy id / probe / scope / spec, but the ecosystem
        # targets change -> the relation now matches "widget".
        self._patch_catalog(
            self._eco_rev_policy(
                (EcosystemTargetSpec(target="example-org/widget", aliases=("widget",)),)
            )
        )
        second = run()
        self.assertEqual(second.status, "success")

        conn = S._open(self.db)
        try:
            discovery_id = conn.execute(
                "SELECT id FROM candidate_discovery"
            ).fetchone()[0]
            after_two = conn.execute(
                "SELECT id, policy_version FROM candidate_assessment "
                "WHERE candidate_discovery_id = ? ORDER BY assessed_at, id",
                (discovery_id,),
            ).fetchall()
            # Two DISTINCT revisions, both retained.
            self.assertEqual(len(after_two), 2)
            self.assertNotEqual(after_two[0]["id"], after_two[1]["id"])
            for row in after_two:
                self.assertEqual(row["policy_version"], "candidate-gate-v3")
            # The selection points at the LATEST revision and its decision
            # matches the selection's stored qualification_decision.
            sel = conn.execute(
                "SELECT winning_assessment_id, qualification_decision "
                "FROM github_candidate_selection ORDER BY selection_rank"
            ).fetchall()
            self.assertEqual(len(sel), 2)  # one selection per run
            for srow in sel:
                decision = conn.execute(
                    "SELECT decision FROM candidate_assessment WHERE id = ?",
                    (srow["winning_assessment_id"],),
                ).fetchone()[0]
                self.assertEqual(decision, srow["qualification_decision"])
            # Attributes must never contain the alias list.
            attrs = json.dumps(
                [json.loads(row["attributes"]) for row in
                 conn.execute("SELECT attributes FROM candidate_assessment")]
            )
            self.assertNotIn("aliases", attrs)
        finally:
            conn.close()

        # Run 3: same relation (core) but a different policy context
        # (candidate_limit 49 -> different policy_hash) -> another revision.
        self._patch_catalog(
            self._eco_rev_policy(
                (EcosystemTargetSpec(target="example-org/core", aliases=("core",)),),
                candidate_limit=49,
            )
        )
        third = run()
        self.assertEqual(third.status, "success")
        conn = S._open(self.db)
        try:
            discovery_id = conn.execute(
                "SELECT id FROM candidate_discovery"
            ).fetchone()[0]
            revisions = conn.execute(
                "SELECT id FROM candidate_assessment "
                "WHERE candidate_discovery_id = ?",
                (discovery_id,),
            ).fetchall()
            self.assertEqual(len(revisions), 3)
            self.assertEqual(len({row["id"] for row in revisions}), 3)
        finally:
            conn.close()

    def test_legacy_v2_assessment_identity_unchanged_without_context(self):
        # A 2C1-style qualify call (no resolver, no discovery_context) must
        # keep the legacy input_hash semantics: rerun adds no revision.
        from ai_signal.pipeline.qualify import FIXTURE_HOSTS, qualify_github

        # Seed one observation chain directly (mirrors test_candidate_pipeline).
        payload = {
            "id": 90099,
            "full_name": "example-org/legacy-check",
            "html_url": "https://example.com/example-org/legacy-check",
            "description": (
                "An AI agent harness with a substantive description long "
                "enough for the gate to consider it properly."
            ),
            "topics": ["agent"],
            "stargazers_count": 1,
            "created_at": "2026-06-01T00:00:00+00:00",
            "updated_at": "2026-08-10T00:00:00+00:00",
            "pushed_at": "2026-08-10T00:00:00+00:00",
        }
        S.initialize_database(self.db)
        conn = S._open(self.db)
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO collection_run (id, week_key, started_at, status, "
            "config_snapshot, created_at) VALUES (?,?,?,?,?,?)",
            ("cr-legacy", "2026-W33", CLOCK, "success", "{}", CLOCK),
        )
        conn.execute(
            "INSERT INTO source_run (id, collection_run_id, source, scope_key, "
            "source_version, status, started_at, finished_at, item_count, "
            "warning_count, warnings, cursor_advanced, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("sr-legacy", "cr-legacy", "github", "legacy-scope-v1",
             "github-rest-v1", "success", CLOCK, CLOCK, 1, 0, "[]", 0, CLOCK),
        )
        text = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"))
        psha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        raw_id = "r" * 64
        conn.execute(
            "INSERT INTO raw_signal (id, collection_run_id, source, external_id, "
            "payload, payload_sha256, source_version, collected_at, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (raw_id, "cr-legacy", "github", "90099", text, psha,
             "github-rest-v1", CLOCK, CLOCK),
        )
        conn.execute(
            "INSERT INTO raw_signal_observation (source_run_id, raw_signal_id, "
            "observed_at, created_at) VALUES (?,?,?,?)",
            ("sr-legacy", raw_id, CLOCK, CLOCK),
        )
        conn.execute("COMMIT")
        conn.close()

        conn = S._open(self.db)
        conn.execute("BEGIN")
        first = qualify_github(
            conn, "2026-W33", "legacy-scope-v1", "emerging", 50,
            safe_hosts=FIXTURE_HOSTS,
        )
        second = qualify_github(
            conn, "2026-W33", "legacy-scope-v1", "emerging", 50,
            safe_hosts=FIXTURE_HOSTS,
        )
        conn.execute("COMMIT")
        conn.close()
        self.assertEqual(first.assessments_created, 1)
        self.assertEqual(second.assessments_created, 0)
        conn = S._open(self.db)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM candidate_assessment"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 1)
