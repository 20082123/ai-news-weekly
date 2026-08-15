"""Golden Case regression tests for the 2C2 discovery pipeline (phase 2C2-D).

These four cases are the agreed acceptance anchors from the 2C2 plan
(handover section 12). Each test is named after its case; all run fully
offline through a fake transport.
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


def _repo_item(repo_id, **overrides):
    payload = {
        "id": repo_id,
        "full_name": "example-org/repo-%d" % repo_id,
        "html_url": "https://github.com/example-org/repo-%d" % repo_id,
        "description": (
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
        "fork": False,
        "archived": False,
        "disabled": False,
        "is_template": False,
    }
    payload.update(overrides)
    return payload


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


class _FakeTransport:
    def __init__(self, pages_by_query):
        self.pages_by_query = pages_by_query
        self.calls = []

    def get(self, url, headers, timeout_seconds, max_response_bytes):
        self.calls.append(url)
        query = parse_qs(urlparse(url).query).get("q", [""])[0]
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


class GoldenCasesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai_golden_")
        self.db = os.path.join(self.tmp, "test.db")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _patch_catalog(self, policy):
        patcher = mock.patch.object(
            policy_module, "POLICY_CATALOG", {policy.id: policy}
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, policy_id, transport):
        return run_github_discovery(
            self.db,
            "2026-W33",
            policy_id,
            allow_network=True,
            transport_factory=lambda: transport,
            clock=lambda: _utc(CLOCK),
        )

    def _assert_no_legacy_entities(self):
        conn = S._open(self.db)
        try:
            for table in ("signal", "event", "event_member", "claim",
                          "evidence", "claim_evidence", "material_pack"):
                count = conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
                self.assertEqual(count, 0, "2C2 must never write %s" % table)
        finally:
            conn.close()

    # ------------------------------------------------------------------ #
    def test_golden_case_a_garbage_repo_never_enters_queue(self):
        """1 star, generic AI description, recent update, no real agent/work
        value: may be recalled, but ends WATCH/REJECT - never in the queue."""
        garbage = _repo_item(
            1,
            description=(
                "A generic AI powered platform for the future of intelligent "
                "work, delivering next-generation productivity to everyone."
            ),
            topics=("ai", "platform"),
            stargazers_count=1,
        )
        fork_junk = _repo_item(2, fork=True)
        self._patch_catalog(
            GitHubDiscoveryPolicy(
                id="emerging-v1",
                lane="emerging",
                probes=(_search_probe("emerging-v1-q1", "ghp-golden-a-q1",
                                      "topic:ai-agent pushed:>2026-07-01", 0),),
                candidate_limit=100,
                research_budget=8,
            )
        )
        result = self._run(
            "emerging-v1",
            _FakeTransport({
                "topic:ai-agent pushed:>2026-07-01": [garbage, fork_junk]
            }),
        )
        self.assertEqual(result.status, "success")
        # Generic repo: no agent relevance -> WATCH. Fork -> REJECT.
        self.assertEqual(result.watch, 1)
        self.assertEqual(result.rejected, 1)
        self.assertEqual(result.research, 0)
        self.assertEqual(result.selections_total, 0)
        self._assert_no_legacy_entities()

    def test_golden_case_b_adhd_one_like_emerging_research(self):
        """Emerging lane: 1 star + new + active + agent-related -> RESEARCH."""
        adhd_like = _repo_item(
            90001,
            description=(
                "A Windows desktop application that integrates multiple AI "
                "models as a personal harness for focus and task management."
            ),
            topics=("windows", "desktop", "ai-harness", "productivity"),
            stargazers_count=1,
        )
        self._patch_catalog(
            GitHubDiscoveryPolicy(
                id="emerging-v1",
                lane="emerging",
                probes=(_search_probe("emerging-v1-q1", "ghp-golden-b-q1",
                                      "topic:ai-agent pushed:>2026-07-01", 0),),
                candidate_limit=100,
                research_budget=8,
            )
        )
        result = self._run(
            "emerging-v1",
            _FakeTransport({
                "topic:ai-agent pushed:>2026-07-01": [adhd_like]
            }),
        )
        self.assertEqual(result.research, 1)
        self.assertEqual(result.selections_total, 1)
        self.assertEqual(result.queued, 1)
        self._assert_no_legacy_entities()

    def test_golden_case_b_ecosystem_relation_research(self):
        """Ecosystem lane: description clearly mentions the core project ->
        relation resolves to the raw signal -> RESEARCH."""
        wrapper = _repo_item(
            90002,
            description=(
                "A deployment dashboard around core that manages model "
                "routing and cost tracking for agent teams."
            ),
        )
        self._patch_catalog(
            GitHubDiscoveryPolicy(
                id="ecosystem-v1",
                lane="ecosystem",
                probes=(_search_probe("ecosystem-v1-q1", "ghp-golden-b-eco-q1",
                                      "core in:name,description pushed:>2026-07-01", 0),),
                candidate_limit=50,
                research_budget=5,
                ecosystem_targets=(
                    EcosystemTargetSpec(target="example-org/core", aliases=("core",)),
                ),
            )
        )
        result = self._run(
            "ecosystem-v1",
            _FakeTransport({
                "core in:name,description pushed:>2026-07-01": [wrapper]
            }),
        )
        self.assertEqual(result.research, 1)
        self.assertEqual(result.selections_total, 1)
        conn = S._open(self.db)
        try:
            row = conn.execute(
                "SELECT relation_kind, relation_raw_signal_id "
                "FROM github_candidate_selection"
            ).fetchone()
            self.assertEqual(row["relation_kind"], "description_mention")
            self.assertIsNotNone(row["relation_raw_signal_id"])
        finally:
            conn.close()
        self._assert_no_legacy_entities()

    def test_golden_case_c_mature_agent_research_not_event(self):
        """Mature lane: high stars + agent-related + recent activity ->
        RESEARCH - and still NOT an Event / Material on this path."""
        mature_agent = _repo_item(
            90003,
            stargazers_count=1500,
            description=(
                "A mature multi-agent coding framework used by teams to "
                "plan, edit and review large codebases automatically."
            ),
        )
        self._patch_catalog(
            GitHubDiscoveryPolicy(
                id="mature-v1",
                lane="mature",
                probes=(_search_probe("mature-v1-q1", "ghp-golden-c-q1",
                                      "topic:ai-agent stars:>=100 pushed:>2026-07-01", 0),),
                candidate_limit=50,
                research_budget=5,
            )
        )
        result = self._run(
            "mature-v1",
            _FakeTransport({
                "topic:ai-agent stars:>=100 pushed:>2026-07-01": [mature_agent]
            }),
        )
        self.assertEqual(result.research, 1)
        self.assertEqual(result.selections_total, 1)
        self._assert_no_legacy_entities()

    def test_golden_case_d_watchlist_ignores_stars_and_topics(self):
        """Watchlist lane: human-confirmed target + fresh observation ->
        qualification, independent of stars/topics."""

        class _ReposTransport:
            def __init__(self, repos_by_full_name):
                self.repos_by_full_name = repos_by_full_name
                self.calls = []

            def get(self, url, headers, timeout_seconds, max_response_bytes):
                self.calls.append(url)
                full_name = urlparse(url).path[len("/repos/"):]
                repo = self.repos_by_full_name.get(full_name)
                if repo is None:
                    raise RuntimeError("unexpected full_name")
                body = json.dumps(repo).encode("utf-8")
                return HttpResponse(
                    status=200,
                    headers={"Content-Type": "application/json"},
                    body=body,
                    final_url="https://api.github.com" + urlparse(url).path,
                )

        target = _repo_item(
            90004,
            description=(
                "A Windows desktop application that integrates multiple AI "
                "models as a personal harness for focus and task management."
            ),
            topics=("windows",),
            stargazers_count=1,
        )
        self._patch_catalog(
            GitHubDiscoveryPolicy(
                id="watchlist-v1",
                lane="watchlist",
                probes=(
                    GitHubDiscoveryProbe(
                        probe_id="watchlist-v1-r1",
                        kind="watchlist_target",
                        scope_key="ghp-golden-d-r1",
                        spec={"full_name": "example-org/repo-90004"},
                        priority=0,
                    ),
                ),
                candidate_limit=20,
                research_budget=5,
            )
        )
        transport = _ReposTransport(
            repos_by_full_name={"example-org/repo-90004": target}
        )
        result = self._run("watchlist-v1", transport)
        self.assertEqual(result.research, 1)
        self.assertEqual(result.selections_total, 1)
        self._assert_no_legacy_entities()


if __name__ == "__main__":
    unittest.main()
