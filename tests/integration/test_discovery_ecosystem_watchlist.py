"""Integration tests for 2C2-C: watchlist direct snapshots + ecosystem relation.

Offline only: a fake transport answers both the Search API and the repos API,
so no test reaches the network. ``allow_network=True`` is passed exactly as
the CLI will after its own gate.
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
from ai_signal.discovery.relation import EcosystemTargetSpec  # noqa: E402
from ai_signal.discovery.run import run_github_discovery  # noqa: E402
from ai_signal.sources.github_rest import HttpResponse  # noqa: E402
from ai_signal.storage import sqlite as S  # noqa: E402

CLOCK = "2026-08-15T08:00:00+00:00"


def _utc(text):
    return datetime.fromisoformat(text)


def _repo_item(repo_id, *, fork=False, description=None, topics=("agent",)):
    return {
        "id": repo_id,
        "full_name": "example-org/repo-%d" % repo_id,
        "html_url": "https://github.com/example-org/repo-%d" % repo_id,
        "description": description or (
            "An AI agent harness that wraps multiple models for real task "
            "automation with a substantive enough description for the gate."
        ),
        "topics": list(topics),
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


def _watchlist_probe(probe_id, scope_key, full_name, priority):
    return GitHubDiscoveryProbe(
        probe_id=probe_id,
        kind="watchlist_target",
        scope_key=scope_key,
        spec={"full_name": full_name},
        priority=priority,
    )


class _RoutingFakeTransport:
    """Answers both api endpoints and records every call."""

    def __init__(self, search_pages=None, repos_by_full_name=None):
        self.search_pages = search_pages or {}
        self.repos_by_full_name = repos_by_full_name or {}
        self.calls = []

    def get(self, url, headers, timeout_seconds, max_response_bytes):
        self.calls.append(url)
        parsed = urlparse(url)
        if parsed.path == "/search/repositories":
            query = parse_qs(parsed.query).get("q", [""])[0]
            items = self.search_pages.get(query)
            if items is None:
                raise RuntimeError("unexpected query")
            body = json.dumps({"total_count": len(items), "items": items}).encode("utf-8")
        elif parsed.path.startswith("/repos/"):
            full_name = parsed.path[len("/repos/"):]
            repo = self.repos_by_full_name.get(full_name)
            if repo is None:
                raise RuntimeError("unexpected full_name")
            body = json.dumps(repo).encode("utf-8")
        else:
            raise RuntimeError("unexpected path")
        return HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=body,
            final_url="https://api.github.com" + parsed.path,
        )


class WatchlistEcosystemTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai_disc_2c2c_")
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

    def _run(self, policy, transport):
        return run_github_discovery(
            self.db,
            "2026-W33",
            policy.id,
            allow_network=True,
            transport_factory=lambda: transport,
            clock=lambda: _utc(CLOCK),
        )

    # ------------------------------------------------------------------ #
    # Golden Case D: watchlist - human-confirmed targets qualify on their
    # own merit, independent of stars/topics.
    # ------------------------------------------------------------------ #
    def test_watchlist_direct_snapshot_qualifies(self):
        repo = _repo_item(1, description=(
            "A Windows desktop application that integrates multiple AI models "
            "as a personal harness for focus and task management."
        ), topics=("windows",))
        transport = _RoutingFakeTransport(
            repos_by_full_name={"example-org/repo-1": repo}
        )
        policy = self._patch_catalog(
            GitHubDiscoveryPolicy(
                id="watchlist-v1",
                lane="watchlist",
                probes=(_watchlist_probe("watchlist-v1-r1",
                                         "ghp-watchlist-v1-r1",
                                         "example-org/repo-1", 0),),
                candidate_limit=20,
                research_budget=5,
            )
        )
        result = self._run(policy, transport)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.research, 1)
        self.assertEqual(result.selections_total, 1)
        self.assertEqual(len(transport.calls), 1)
        conn = S._open(self.db)
        try:
            # repos adapter: full_name never stored, only its hash.
            cfg = conn.execute(
                "SELECT config_snapshot FROM collection_run"
            ).fetchone()[0]
            snapshot = json.loads(cfg)
            self.assertEqual(snapshot["adapter_kind"], "github-repos-v1")
            self.assertIn("full_name_sha256", snapshot)
            self.assertIsNone(snapshot.get("full_name"))
            self.assertNotIn("example-org/repo-1", json.dumps(snapshot))
            # No pagination cursor is ever created for a direct snapshot.
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM source_cursor").fetchone()[0], 0
            )
            # Gate v2 used (no resolver supplied).
            row = conn.execute(
                "SELECT policy_version FROM candidate_assessment"
            ).fetchone()
            self.assertEqual(row["policy_version"], "candidate-gate-v2")
        finally:
            conn.close()

    def test_watchlist_stale_repo_stays_watch(self):
        stale = dict(
            _repo_item(2),
            pushed_at="2026-05-01T00:00:00+00:00",
            updated_at="2026-05-01T00:00:00+00:00",
        )
        transport = _RoutingFakeTransport(
            repos_by_full_name={"example-org/repo-2": stale}
        )
        policy = self._patch_catalog(
            GitHubDiscoveryPolicy(
                id="watchlist-v1",
                lane="watchlist",
                probes=(_watchlist_probe("watchlist-v1-r1",
                                         "ghp-watchlist-v1-r2",
                                         "example-org/repo-2", 0),),
                candidate_limit=20,
                research_budget=5,
            )
        )
        result = self._run(policy, transport)
        self.assertEqual(result.watch, 1)
        self.assertEqual(result.selections_total, 0)

    # ------------------------------------------------------------------ #
    # Golden Case B (ecosystem): description relation -> RESEARCH with
    # relation evidence; query hit without relation stays WATCH.
    # ------------------------------------------------------------------ #
    def _ecosystem_policy(self):
        return GitHubDiscoveryPolicy(
            id="ecosystem-v1",
            lane="ecosystem",
            probes=(_search_probe(
                "ecosystem-v1-q1", "ghp-eco-v1-q1",
                "core in:name,description pushed:>2026-07-01", 0,
            ),),
            candidate_limit=50,
            research_budget=5,
            ecosystem_targets=(
                EcosystemTargetSpec(
                    target="example-org/core", aliases=("core", "example-core")
                ),
            ),
        )

    def test_ecosystem_relation_research_with_evidence(self):
        related = _repo_item(11, description=(
            "A dashboard UI around core that visualizes runs and costs for "
            "teams, with enough substance for the gate."
        ))
        unrelated = _repo_item(12, description=(
            "A completely unrelated task manager with enough substance."
        ))
        transport = _RoutingFakeTransport(
            search_pages={
                "core in:name,description pushed:>2026-07-01": [related, unrelated]
            }
        )
        policy = self._patch_catalog(self._ecosystem_policy())
        result = self._run(policy, transport)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.research, 1)
        self.assertEqual(result.watch, 1)
        self.assertEqual(result.selections_total, 1)

        conn = S._open(self.db)
        try:
            sel = conn.execute(
                "SELECT ecosystem_target, relation_kind, relation_field, "
                "relation_raw_signal_id, qualification_decision "
                "FROM github_candidate_selection"
            ).fetchone()
            self.assertEqual(sel["ecosystem_target"], "example-org/core")
            self.assertEqual(sel["relation_kind"], "description_mention")
            self.assertEqual(sel["relation_field"], "description")
            self.assertIsNotNone(sel["relation_raw_signal_id"])
            self.assertEqual(sel["qualification_decision"], "research")
            # Gate v3 with the relation reason code.
            assessment = conn.execute(
                "SELECT policy_version, reason_codes FROM candidate_assessment ca "
                "JOIN candidate_discovery cd ON cd.id = ca.candidate_discovery_id "
                "JOIN candidate c ON c.id = cd.candidate_id "
                "WHERE c.title = 'example-org/repo-11'"
            ).fetchone()
            self.assertEqual(assessment["policy_version"], "candidate-gate-v3")
            self.assertIn("ecosystem_relation_match", json.loads(assessment["reason_codes"]))
            # The unrelated query hit stayed WATCH with the missing-relation code.
            other = conn.execute(
                "SELECT decision, reason_codes FROM candidate_assessment ca "
                "JOIN candidate_discovery cd ON cd.id = ca.candidate_discovery_id "
                "JOIN candidate c ON c.id = cd.candidate_id "
                "WHERE c.title = 'example-org/repo-12'"
            ).fetchone()
            self.assertEqual(other["decision"], "watch")
            self.assertIn("missing_ecosystem_relation", json.loads(other["reason_codes"]))
        finally:
            conn.close()

    def test_ecosystem_relation_thin_description_stays_watch(self):
        thin = _repo_item(21, description="core wrapper.")
        transport = _RoutingFakeTransport(
            search_pages={
                "core in:name,description pushed:>2026-07-01": [thin]
            }
        )
        policy = self._patch_catalog(self._ecosystem_policy())
        result = self._run(policy, transport)
        # Relation exists but the substantive-description baseline fails:
        # WATCH, not RESEARCH - the relation alone cannot carry the decision.
        self.assertEqual(result.research, 0)
        self.assertEqual(result.watch, 1)
        self.assertEqual(result.selections_total, 0)

    def test_ecosystem_targets_rejected_on_other_lanes(self):
        with self.assertRaises(Exception):
            GitHubDiscoveryPolicy(
                id="bad-v1",
                lane="emerging",
                probes=(),
                candidate_limit=20,
                research_budget=5,
                ecosystem_targets=(
                    EcosystemTargetSpec(target="example-org/core"),),
            )

    # ------------------------------------------------------------------ #
    # Real catalog regression anchors (draft first-user lists, 2026-08-16).
    # ------------------------------------------------------------------ #
    def test_real_watchlist_catalog_end_to_end(self):
        policy = policy_module.get_policy("watchlist-v1")
        repos = {}
        for index, probe in enumerate(policy.probes):
            full_name = probe.spec["full_name"]
            payload = _repo_item(80000 + index)
            payload["full_name"] = full_name
            payload["html_url"] = "https://github.com/" + full_name
            repos[full_name] = payload
        transport = _RoutingFakeTransport(repos_by_full_name=repos)
        result = run_github_discovery(
            self.db,
            "2026-W33",
            "watchlist-v1",
            allow_network=True,
            transport_factory=lambda: transport,
            clock=lambda: _utc(CLOCK),
        )
        self.assertEqual(result.status, "success")
        self.assertEqual(result.probes_total, 5)
        self.assertEqual(result.research, 5)
        self.assertEqual(result.selections_total, 5)
        self.assertEqual(result.queued, 5)  # budget is 5
        self.assertEqual(len(transport.calls), 5)
        conn = S._open(self.db)
        try:
            probe_kinds = {
                row["kind"]
                for row in conn.execute(
                    "SELECT kind FROM github_discovery_probe_run"
                )
            }
            self.assertEqual(probe_kinds, {"watchlist_target"})
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM source_cursor").fetchone()[0], 0
            )
        finally:
            conn.close()

    def test_real_ecosystem_catalog_end_to_end(self):
        policy = policy_module.get_policy("ecosystem-v1")
        related = _repo_item(31, description=(
            "A deployment and observability wrapper around langgraph for "
            "agent teams, with enough substance for the gate."
        ))
        unrelated = _repo_item(32, description=(
            "A completely unrelated task manager with plenty of substance "
            "for the gate check to pass its description baseline."
        ))
        pages = {}
        for probe in policy.probes:
            items = [related, unrelated] if probe.priority == 0 else [related]
            pages[probe.spec["query"]] = items
        transport = _RoutingFakeTransport(search_pages=pages)
        result = run_github_discovery(
            self.db,
            "2026-W33",
            "ecosystem-v1",
            allow_network=True,
            transport_factory=lambda: transport,
            clock=lambda: _utc(CLOCK),
        )
        self.assertEqual(result.status, "success")
        # research counts per-scope assessments: the related repo qualifies in
        # BOTH probes (2), the unrelated one stays watch in the first probe.
        self.assertEqual(result.research, 2)
        self.assertEqual(result.watch, 1)
        # Candidate-level dedup happens at selection time: one final row.
        self.assertEqual(result.selections_total, 1)
        conn = S._open(self.db)
        try:
            row = conn.execute(
                "SELECT ecosystem_target, relation_kind, relation_raw_signal_id "
                "FROM github_candidate_selection"
            ).fetchone()
            self.assertEqual(row["ecosystem_target"], "langchain-ai/langgraph")
            self.assertEqual(row["relation_kind"], "description_mention")
            self.assertIsNotNone(row["relation_raw_signal_id"])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
