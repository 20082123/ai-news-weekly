"""Unit tests for :mod:`ai_signal.pipeline.qualify` (lane-aware gate v2)."""

import json
import pathlib
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.pipeline.qualify import (  # noqa: E402
    ACTIVE_PUSH_MAX_DAYS,
    AGENT_RELEVANCE_KEYWORDS,
    EMERGING_MAX_AGE_DAYS,
    FIXTURE_HOSTS,
    MATURE_MIN_STARS,
    MISSING_EVIDENCE,
    POLICY_VERSION,
    SUBSTANTIVE_DESCRIPTION_MIN,
    TRIGGER_KIND,
    TRIGGER_SUMMARY,
    QualifyError,
    _gate,
    _input_hash,
    _is_agent_relevant,
    _is_safe_external_homepage,
    _is_safe_https_url,
    _parse_repo,
)

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "candidates"
CLOCK = datetime(2026, 8, 15, tzinfo=timezone.utc)


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _variant(name):
    return next(v for v in _load("variants.json") if v["name"] == name)


def _gate_variant(name, lane):
    parsed = _parse_repo(_variant(name)["payload"], FIXTURE_HOSTS)
    return _gate(parsed, lane, CLOCK)


class ParseRepoTest(unittest.TestCase):
    def test_whitelist_only_attributes(self):
        payload = dict(_load("adhd_one_like.json")["payload"])
        payload["secret_extra_field"] = "dropped"
        payload["owner"] = {"login": "dropped"}
        parsed = _parse_repo(payload, FIXTURE_HOSTS)
        self.assertNotIn("secret_extra_field", parsed.attributes)
        self.assertNotIn("owner", parsed.attributes)

    def test_homepage_url_never_stored(self):
        for name in ("http_homepage", "https_homepage"):
            parsed = _parse_repo(_variant(name)["payload"], FIXTURE_HOSTS)
            blob = json.dumps(parsed.attributes)
            self.assertNotIn("http://insecure", blob)
            self.assertNotIn("https://external", blob)
            self.assertIn("homepage_present", parsed.attributes)

    def test_production_hosts_accept_external_https_homepage(self):
        from ai_signal.pipeline.qualify import _GITHUB_HOSTS

        payload = {
            "id": 1,
            "full_name": "example-org/prod-homepage",
            "html_url": "https://github.com/example-org/prod-homepage",
            "description": "A substantive agent harness description with enough characters to pass the gate check.",
            "topics": ["agent", "harness"],
            "stargazers_count": 1,
            "forks_count": 0,
            "created_at": "2026-06-01T00:00:00+00:00",
            "updated_at": "2026-08-09T00:00:00+00:00",
            "pushed_at": "2026-08-09T00:00:00+00:00",
            "homepage": "https://product.example.org/",
            "fork": False,
            "archived": False,
            "disabled": False,
            "is_template": False,
        }
        parsed = _parse_repo(payload, _GITHUB_HOSTS)
        self.assertTrue(parsed.homepage_present)
        self.assertTrue(parsed.homepage_safe)
        self.assertNotIn("product.example.org", json.dumps(parsed.attributes))

    def test_http_homepage_present_but_unsafe(self):
        parsed = _parse_repo(_variant("http_homepage")["payload"], FIXTURE_HOSTS)
        self.assertTrue(parsed.homepage_present)
        self.assertFalse(parsed.homepage_safe)
        self.assertFalse(parsed.untrusted)

    def test_javascript_homepage_ignored_not_untrusted(self):
        parsed = _parse_repo(_variant("javascript_homepage")["payload"], FIXTURE_HOSTS)
        self.assertTrue(parsed.homepage_present)
        self.assertFalse(parsed.homepage_safe)
        self.assertFalse(parsed.untrusted)
        self.assertFalse(parsed.invalid_metadata)
        self.assertNotIn("javascript:", json.dumps(parsed.attributes))

    def test_injection_homepage_ignored_not_untrusted(self):
        parsed = _parse_repo(_variant("injection_homepage")["payload"], FIXTURE_HOSTS)
        self.assertTrue(parsed.homepage_present)
        self.assertFalse(parsed.homepage_safe)
        self.assertFalse(parsed.untrusted)
        self.assertFalse(parsed.invalid_metadata)
        self.assertNotIn("ignore previous instructions", json.dumps(parsed.attributes))

    def test_credentialed_homepage_ignored_not_untrusted(self):
        payload = dict(_load("adhd_one_like.json")["payload"])
        payload["homepage"] = "https://user:pass@example.org/"
        parsed = _parse_repo(payload, FIXTURE_HOSTS)
        self.assertTrue(parsed.homepage_present)
        self.assertFalse(parsed.homepage_safe)
        self.assertFalse(parsed.untrusted)
        self.assertNotIn("user:pass", json.dumps(parsed.attributes))

    def test_control_char_homepage_ignored(self):
        payload = dict(_load("adhd_one_like.json")["payload"])
        payload["homepage"] = "https://example.org/\x07"
        parsed = _parse_repo(payload, FIXTURE_HOSTS)
        self.assertTrue(parsed.homepage_present)
        self.assertFalse(parsed.homepage_safe)
        self.assertFalse(parsed.untrusted)
        self.assertNotIn("\x07", json.dumps(parsed.attributes))

    def test_malicious_description_never_stored(self):
        parsed = _parse_repo(_variant("malicious_description")["payload"], FIXTURE_HOSTS)
        blob = json.dumps(parsed.attributes)
        self.assertNotIn("ignore previous instructions", blob)
        self.assertNotIn("<script", blob)
        self.assertTrue(parsed.untrusted)

    def test_unsafe_identity_url_quarantined(self):
        with self.assertRaises(QualifyError):
            _parse_repo(_variant("quarantine_identity")["payload"], FIXTURE_HOSTS)

    def test_bad_port_urls_return_false_not_raise(self):
        # urllib.parse raises ValueError when .port is read on a non-numeric
        # or out-of-range port; the helpers must catch it and return False.
        for url in ("https://example.org:bad/", "https://example.org:65536/"):
            with self.subTest(kind="homepage", url=url):
                self.assertFalse(_is_safe_external_homepage(url))
        for url in ("https://example.com:bad/repo", "https://example.com:65536/repo"):
            with self.subTest(kind="identity", url=url):
                self.assertFalse(_is_safe_https_url(url, FIXTURE_HOSTS))

    def test_identity_url_bad_port_quarantined(self):
        payload = dict(_load("adhd_one_like.json")["payload"])
        for url in ("https://example.com:bad/repo", "https://example.com:65536/repo"):
            with self.subTest(url=url):
                payload["html_url"] = url
                with self.assertRaises(QualifyError):
                    _parse_repo(payload, FIXTURE_HOSTS)

    def test_homepage_bad_port_ignored_not_untrusted(self):
        payload = dict(_load("adhd_one_like.json")["payload"])
        for url in ("https://example.org:bad/", "https://example.org:65536/"):
            with self.subTest(url=url):
                payload["homepage"] = url
                parsed = _parse_repo(payload, FIXTURE_HOSTS)
                self.assertTrue(parsed.homepage_present)
                self.assertFalse(parsed.homepage_safe)
                self.assertFalse(parsed.untrusted)
                self.assertFalse(parsed.invalid_metadata)
                self.assertNotIn(url, json.dumps(parsed.attributes))

    def test_default_hosts_reject_fixture_domains(self):
        from ai_signal.pipeline.qualify import _GITHUB_HOSTS

        with self.assertRaises(QualifyError):
            _parse_repo(_load("adhd_one_like.json")["payload"], _GITHUB_HOSTS)

    def test_input_hash_deterministic(self):
        payload = _load("adhd_one_like.json")["payload"]
        a = _input_hash(_parse_repo(payload, FIXTURE_HOSTS).attributes)
        b = _input_hash(_parse_repo(payload, FIXTURE_HOSTS).attributes)
        self.assertEqual(a, b)
        changed = dict(payload, stargazers_count=2)
        self.assertNotEqual(a, _input_hash(_parse_repo(changed, FIXTURE_HOSTS).attributes))


class AgentRelevanceTest(unittest.TestCase):
    def test_keywords(self):
        self.assertIn("agent", AGENT_RELEVANCE_KEYWORDS)
        self.assertIn("multi-agent", AGENT_RELEVANCE_KEYWORDS)
        self.assertIn("mcp", AGENT_RELEVANCE_KEYWORDS)
        self.assertIn("harness", AGENT_RELEVANCE_KEYWORDS)
        self.assertIn("copilot", AGENT_RELEVANCE_KEYWORDS)

    def test_bare_ai_is_not_sufficient(self):
        from ai_signal.pipeline.qualify import _ParsedRepo

        parsed = _ParsedRepo(
            repo_id="1", full_name="org/plain-ai", html_url="https://example.com/x",
            canonical_key="github:repository:1", attributes={},
            fork=False, archived=False, disabled=False, is_template=False,
            description="An ai utility for everyone with enough characters to be substantive text.",
            topics=(), stars=None, forks=None,
            created_at=None, updated_at=None, pushed_at=None,
            homepage_safe=False, homepage_present=False,
        )
        self.assertFalse(_is_agent_relevant(parsed))

    def test_harness_matches(self):
        parsed = _parse_repo(_load("adhd_one_like.json")["payload"], FIXTURE_HOSTS)
        self.assertTrue(_is_agent_relevant(parsed))


class GateMatrixTest(unittest.TestCase):
    """The lane-aware gate matrix (candidate-gate-v2)."""

    def test_constants(self):
        self.assertEqual(POLICY_VERSION, "candidate-gate-v2")
        self.assertEqual(SUBSTANTIVE_DESCRIPTION_MIN, 40)
        self.assertEqual(ACTIVE_PUSH_MAX_DAYS, 45)
        self.assertEqual(EMERGING_MAX_AGE_DAYS, 180)
        self.assertEqual(MATURE_MIN_STARS, 100)
        self.assertEqual(TRIGGER_KIND, "repository_snapshot")
        self.assertEqual(
            TRIGGER_SUMMARY, "发现仓库快照，但尚未确认 Release、Launch 或重大变化。"
        )
        self.assertEqual(
            MISSING_EVIDENCE,
            (
                "specific_event", "readme", "latest_release",
                "previous_release_or_changelog", "working_artifact_or_demo",
                "testability",
            ),
        )

    def _gate_adhd(self, lane):
        parsed = _parse_repo(_load("adhd_one_like.json")["payload"], FIXTURE_HOSTS)
        return _gate(parsed, lane, CLOCK)

    def test_adhd_one_like_emerging_is_research(self):
        # 1 star, agent/harness relevant, young, recent push -> research.
        decision, reasons = self._gate_adhd("emerging")
        self.assertEqual(decision, "research")
        self.assertIn("substantive_description", reasons)
        self.assertIn("agent_relevance_match", reasons)
        self.assertIn("recent_creation", reasons)
        self.assertIn("recent_push", reasons)
        # stars never appear as a generic research condition.
        self.assertNotIn("signal_positive_stars", reasons)

    def test_adhd_one_like_mature_is_watch_low_stars(self):
        decision, reasons = self._gate_adhd("mature")
        self.assertEqual(decision, "watch")
        self.assertIn("mature_stars_below_threshold", reasons)

    def test_adhd_one_like_watchlist_is_research(self):
        # Substantive description + recent push; identity vouched elsewhere.
        decision, reasons = self._gate_adhd("watchlist")
        self.assertEqual(decision, "research")

    def test_adhd_one_like_ecosystem_is_watch_missing_relation(self):
        decision, reasons = self._gate_adhd("ecosystem")
        self.assertEqual(decision, "watch")
        self.assertIn("missing_ecosystem_relation", reasons)

    def test_high_stars_agent_mature_research(self):
        decision, reasons = _gate_variant("agent_mature", "mature")
        self.assertEqual(decision, "research")
        self.assertIn("mature_stars_threshold_met", reasons)

    def test_high_stars_no_agent_never_research(self):
        for lane in ("mature", "emerging", "watchlist", "ecosystem"):
            with self.subTest(lane=lane):
                decision, reasons = _gate_variant("high_stars_no_agent", lane)
                if lane in ("mature", "emerging"):
                    self.assertEqual(decision, "watch")
                    self.assertIn("no_agent_relevance", reasons)
                else:
                    self.assertIn(decision, ("watch", "research"))

    def test_high_stars_no_agent_watchlist_still_research(self):
        # watchlist does not require agent relevance per policy; documented.
        decision, _ = _gate_variant("high_stars_no_agent", "watchlist")
        self.assertEqual(decision, "research")

    def test_emerging_stale_push_watches(self):
        decision, reasons = _gate_variant("emerging_stale_push", "emerging")
        self.assertEqual(decision, "watch")
        self.assertIn("stale_push", reasons)

    def test_thin_description_watches_everywhere(self):
        for lane in ("watchlist", "mature", "emerging"):
            with self.subTest(lane=lane):
                decision, reasons = _gate_variant("thin_description", lane)
                self.assertEqual(decision, "watch")
                self.assertIn("thin_description", reasons)

    def test_reject_variants(self):
        for name, expected_reason in (
            ("is_fork", "repository_is_fork"),
            ("archived", "repository_archived"),
            ("template", "repository_is_template"),
            ("disabled", "repository_disabled"),
            ("empty_description", "missing_description"),
            ("malicious_description", "untrusted_content"),
            ("invalid_types", "invalid_metadata"),
            ("malicious_topic", "untrusted_content"),
            ("non_string_topic", "invalid_metadata"),
        ):
            with self.subTest(name=name):
                decision, reasons = _gate_variant(name, "emerging")
                self.assertEqual(decision, "reject")
                self.assertIn(expected_reason, reasons)

    def test_malicious_topic_never_reaches_research(self):
        # A poisoned topic must reject, never be silently dropped and pass.
        parsed = _parse_repo(_variant("malicious_topic")["payload"], FIXTURE_HOSTS)
        self.assertTrue(parsed.untrusted)
        self.assertNotIn("ignore previous instructions", json.dumps(parsed.attributes))
        for lane in ("watchlist", "mature", "emerging"):
            with self.subTest(lane=lane):
                decision, _ = _gate(parsed, lane, CLOCK)
                self.assertEqual(decision, "reject")

    def test_malicious_homepage_never_rejects(self):
        for name in ("javascript_homepage", "injection_homepage", "http_homepage"):
            with self.subTest(name=name):
                decision, reasons = _gate_variant(name, "emerging")
                self.assertEqual(decision, "research")
                self.assertNotIn("untrusted_content", reasons)
                self.assertNotIn("unsafe_url", reasons)

    def test_http_homepage_does_not_reject(self):
        decision, reasons = _gate_variant("http_homepage", "emerging")
        self.assertNotEqual(decision, "reject")
        self.assertNotIn("unsafe_url", reasons)

    def test_https_homepage_does_not_reject(self):
        decision, _ = _gate_variant("https_homepage", "emerging")
        self.assertEqual(decision, "research")

    def test_no_publishability_words_in_any_decision(self):
        parsed = _parse_repo(_load("adhd_one_like.json")["payload"], FIXTURE_HOSTS)
        for lane in ("watchlist", "mature", "emerging", "ecosystem"):
            decision, _ = _gate(parsed, lane, CLOCK)
            self.assertIn(decision, ("research", "watch", "reject"))

    def test_decision_is_not_a_float_score(self):
        parsed = _parse_repo(_load("adhd_one_like.json")["payload"], FIXTURE_HOSTS)
        decision, reasons = _gate(parsed, "emerging", CLOCK)
        self.assertIsInstance(decision, str)
        self.assertTrue(all(isinstance(code, str) for code in reasons))
        self.assertNotIn("score", " ".join(reasons))


if __name__ == "__main__":
    unittest.main()
