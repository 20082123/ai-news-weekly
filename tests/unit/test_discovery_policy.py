"""Unit tests for the phase 2C2 GitHub Discovery Policy Catalog."""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.discovery.policy import (  # noqa: E402
    POLICY_CATALOG,
    DiscoveryPolicyError,
    GitHubDiscoveryPolicy,
    GitHubDiscoveryProbe,
    get_policy,
    list_policies,
)


def _search_probe(probe_id="p-v1-q1", scope_key="ghp-p-v1-q1", **overrides):
    spec = {
        "query": "topic:ai-agent stars:>=100 pushed:>2026-07-01",
        "sort": "updated",
        "order": "desc",
        "per_page": 25,
        "max_pages": 3,
    }
    spec.update(overrides.pop("spec", {}))
    fields = {
        "probe_id": probe_id,
        "kind": "search",
        "scope_key": scope_key,
        "spec": spec,
        "priority": 0,
    }
    fields.update(overrides)
    return GitHubDiscoveryProbe(**fields)


class ProbeValidationTest(unittest.TestCase):
    def test_search_probe_spec_hash_deterministic(self):
        probe_a = _search_probe()
        probe_b = _search_probe()
        self.assertEqual(probe_a.spec_hash, probe_b.spec_hash)
        self.assertEqual(len(probe_a.spec_hash), 64)
        int(probe_a.spec_hash, 16)

    def test_spec_hash_changes_when_query_changes(self):
        a = _search_probe()
        b = _search_probe(spec={"query": "agentic pushed:>2026-07-01"})
        self.assertNotEqual(a.spec_hash, b.spec_hash)

    def test_spec_hash_excludes_scope_key(self):
        # scope_key is the binding key itself, not semantic probe content:
        # renaming the scope starts a fresh cursor (by design, version bump).
        a = _search_probe(scope_key="ghp-p-v1-q1")
        b = _search_probe(scope_key="ghp-p-v2-q1")
        self.assertEqual(a.spec_hash, b.spec_hash)

    def test_unknown_fields_rejected(self):
        with self.assertRaises(DiscoveryPolicyError):
            _search_probe(spec={"extra_key": "nope"})

    def test_bad_per_page_and_max_pages_rejected(self):
        for bad in (0, 26, True, "25"):
            with self.assertRaises(DiscoveryPolicyError):
                _search_probe(spec={"per_page": bad})
        for bad in (0, 4, False):
            with self.assertRaises(DiscoveryPolicyError):
                _search_probe(spec={"max_pages": bad})

    def test_control_characters_in_query_rejected(self):
        with self.assertRaises(DiscoveryPolicyError):
            _search_probe(spec={"query": "topic:agent\x00"})

    def test_bad_scope_key_rejected(self):
        with self.assertRaises((TypeError, ValueError)):
            _search_probe(scope_key="Raw Query Here")

    def test_bad_priority_rejected(self):
        for bad in (-1, True, "0"):
            with self.assertRaises(DiscoveryPolicyError):
                _search_probe(priority=bad)

    def test_watchlist_spec_validated(self):
        probe = GitHubDiscoveryProbe(
            probe_id="w-v1-r1",
            kind="watchlist_target",
            scope_key="ghp-watchlist-v1-r1",
            spec={"full_name": "owner/repo"},
            priority=0,
        )
        self.assertEqual(probe.spec, {"full_name": "owner/repo"})
        with self.assertRaises(DiscoveryPolicyError):
            GitHubDiscoveryProbe(
                probe_id="w-v1-r2",
                kind="watchlist_target",
                scope_key="ghp-watchlist-v1-r2",
                spec={"full_name": "not-a-valid-owner-repo"},
                priority=0,
            )

    def test_ecosystem_spec_aliases_validated(self):
        probe = GitHubDiscoveryProbe(
            probe_id="e-v1-t1",
            kind="ecosystem_target",
            scope_key="ghp-eco-v1-t1",
            spec={"target": "owner/core", "aliases": ["core", "owner-core"]},
            priority=0,
        )
        self.assertEqual(probe.spec["aliases"], ["core", "owner-core"])
        with self.assertRaises(DiscoveryPolicyError):
            GitHubDiscoveryProbe(
                probe_id="e-v1-t2",
                kind="ecosystem_target",
                scope_key="ghp-eco-v1-t2",
                spec={"target": "owner/core", "aliases": [123]},
                priority=0,
            )


class PolicyValidationTest(unittest.TestCase):
    def _policy(self, **overrides):
        fields = {
            "id": "mature-v1",
            "lane": "mature",
            "probes": (
                _search_probe("mature-v1-q1", "ghp-mature-v1-q1", priority=0),
                _search_probe("mature-v1-q2", "ghp-mature-v1-q2", priority=1),
            ),
            "candidate_limit": 50,
            "research_budget": 5,
        }
        fields.update(overrides)
        return GitHubDiscoveryPolicy(**fields)

    def test_policy_hash_deterministic_and_covers_query(self):
        a = self._policy()
        b = self._policy()
        self.assertEqual(a.policy_hash, b.policy_hash)
        changed = self._policy(
            probes=(
                _search_probe("mature-v1-q1", "ghp-mature-v1-q1", priority=0,
                              spec={"query": "different query"}),
                _search_probe("mature-v1-q2", "ghp-mature-v1-q2", priority=1),
            )
        )
        self.assertNotEqual(a.policy_hash, changed.policy_hash)

    def test_duplicate_probe_ids_rejected(self):
        with self.assertRaises(DiscoveryPolicyError):
            self._policy(
                probes=(
                    _search_probe("dup", "ghp-a-v1"),
                    _search_probe("dup", "ghp-b-v1"),
                )
            )

    def test_duplicate_scope_keys_rejected(self):
        with self.assertRaises(DiscoveryPolicyError):
            self._policy(
                probes=(
                    _search_probe("p-a", "ghp-same-v1"),
                    _search_probe("p-b", "ghp-same-v1"),
                )
            )

    def test_bad_budgets_rejected(self):
        for bad in (-1, True, "50"):
            with self.assertRaises(DiscoveryPolicyError):
                self._policy(candidate_limit=bad)
        for bad in (-1, True, 1.5):
            with self.assertRaises(DiscoveryPolicyError):
                self._policy(research_budget=bad)

    def test_candidate_limit_bounds(self):
        # 1..100 enforced; booleans rejected.
        for bad in (0, 101):
            with self.assertRaises(DiscoveryPolicyError):
                self._policy(candidate_limit=bad, research_budget=0)
        for ok in (1, 100):
            policy = self._policy(candidate_limit=ok, research_budget=0)
            self.assertEqual(policy.candidate_limit, ok)

    def test_research_budget_bounds(self):
        # 0..candidate_limit enforced: 0 is allowed, above the limit is not.
        with self.assertRaises(DiscoveryPolicyError):
            self._policy(research_budget=51)  # candidate_limit is 50
        self.assertEqual(self._policy(research_budget=0).research_budget, 0)
        self.assertEqual(
            self._policy(research_budget=50).research_budget, 50
        )

    def test_duplicate_priorities_rejected(self):
        with self.assertRaises(DiscoveryPolicyError):
            self._policy(
                probes=(
                    _search_probe("p-a", "ghp-a-v1", priority=0),
                    _search_probe("p-b", "ghp-b-v1", priority=0),
                )
            )

    def test_bad_lane_rejected(self):
        with self.assertRaises(DiscoveryPolicyError):
            self._policy(lane="global")

    def test_policy_frozen(self):
        policy = self._policy()
        with self.assertRaises(Exception):
            policy.candidate_limit = 999  # type: ignore[misc]


class CatalogTest(unittest.TestCase):
    def test_catalog_contains_four_agreed_policies(self):
        self.assertEqual(
            set(POLICY_CATALOG),
            {"watchlist-v1", "mature-v1", "emerging-v1", "ecosystem-v1"},
        )

    def test_agreed_budgets(self):
        expected = {
            "watchlist-v1": (20, 5),
            "mature-v1": (50, 5),
            "emerging-v1": (100, 8),
            "ecosystem-v1": (50, 5),
        }
        for policy_id, (limit, budget) in expected.items():
            with self.subTest(policy_id=policy_id):
                policy = get_policy(policy_id)
                self.assertEqual(policy.candidate_limit, limit)
                self.assertEqual(policy.research_budget, budget)

    def test_mature_and_emerging_have_search_probes(self):
        for policy_id in ("mature-v1", "emerging-v1"):
            with self.subTest(policy_id=policy_id):
                policy = get_policy(policy_id)
                self.assertGreaterEqual(len(policy.probes), 2)
                for probe in policy.probes:
                    self.assertEqual(probe.kind, "search")
                    self.assertLessEqual(probe.spec["per_page"], 25)
                    self.assertLessEqual(probe.spec["max_pages"], 3)

    def test_scope_keys_unique_across_catalog(self):
        scope_keys = [
            probe.scope_key
            for policy in POLICY_CATALOG.values()
            for probe in policy.probes
        ]
        self.assertEqual(len(scope_keys), len(set(scope_keys)))

    def test_watchlist_and_ecosystem_draft_lists_present(self):
        # DRAFT first-user lists (2026-08-16): the engineering shape is
        # locked; only these catalog entries remain to be confirmed/swapped.
        watchlist = get_policy("watchlist-v1")
        self.assertEqual(len(watchlist.probes), 6)
        for probe in watchlist.probes:
            self.assertEqual(probe.kind, "watchlist_target")
            self.assertIn("/", probe.spec["full_name"])
        ecosystem = get_policy("ecosystem-v1")
        self.assertGreaterEqual(len(ecosystem.probes), 1)
        self.assertTrue(all(probe.kind == "search" for probe in ecosystem.probes))
        self.assertEqual(len(ecosystem.ecosystem_targets), 1)
        target = ecosystem.ecosystem_targets[0]
        self.assertEqual(target.target, "langchain-ai/langgraph")
        self.assertTrue(target.aliases)

    def test_unknown_policy_raises(self):
        with self.assertRaises(DiscoveryPolicyError):
            get_policy("nope-v1")

    def test_list_policies_leaks_no_query(self):
        blob = repr(list_policies())
        lowered = blob.lower()
        self.assertNotIn("query", lowered)
        self.assertNotIn("agent", lowered)
        self.assertNotIn("mcp", lowered)

    def test_policy_hash_stable_hex(self):
        for policy in POLICY_CATALOG.values():
            with self.subTest(policy_id=policy.id):
                self.assertEqual(len(policy.policy_hash), 64)
                int(policy.policy_hash, 16)
                # Recomputing on the same definition is stable across calls.
                self.assertEqual(policy.policy_hash, policy.policy_hash)


if __name__ == "__main__":
    unittest.main()
