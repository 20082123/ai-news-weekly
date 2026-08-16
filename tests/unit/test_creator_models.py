"""Unit tests for the creator-centric hub models and gap routing."""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.discovery.gap_routing import (  # noqa: E402
    ALL_GAPS,
    assess_evidence_gaps,
    classify_fact_source,
)
from ai_signal.domain.models import CreatorChoice  # noqa: E402


class CreatorChoiceModelTest(unittest.TestCase):
    def test_identity_over_week_and_subject(self):
        a = CreatorChoice(week_key="2026-W33", subject="DeepSeek 涨价")
        b = CreatorChoice(week_key="2026-W33", subject="DeepSeek 涨价")
        self.assertEqual(a.id, b.id)
        self.assertNotEqual(
            a.id, CreatorChoice(week_key="2026-W33", subject="另一题").id
        )
        self.assertEqual(len(a.id), 64)

    def test_validation(self):
        with self.assertRaises(ValueError):
            CreatorChoice(week_key="", subject="x")
        with self.assertRaises(ValueError):
            CreatorChoice(week_key="2026-W33", subject="  ")
        with self.assertRaises(ValueError):
            CreatorChoice(week_key="2026-W33", subject="x", status="nope")


class GapRoutingTest(unittest.TestCase):
    def test_classify_sources(self):
        self.assertEqual(
            classify_fact_source("github_release", "https://github.com/x"),
            "technical_implementation",
        )
        self.assertEqual(
            classify_fact_source("official_page", "https://openai.com/x"),
            "official_confirmation",
        )
        self.assertEqual(
            classify_fact_source("manual", "https://www.reddit.com/r/x/1"),
            "user_reality",
        )
        self.assertEqual(
            classify_fact_source("manual", "https://x.com/a/status/1"),
            "early_signal",
        )

    def test_all_gaps_open_when_no_facts(self):
        self.assertEqual(
            assess_evidence_gaps([]),
            (
                "official_confirmation",
                "technical_implementation",
                "user_reality",
                "early_signal",
                "personal_testing",
            ),
        )

    def test_gaps_shrink_when_covered(self):
        facts = [
            ("github_release", "https://github.com/x"),
            ("official_page", "https://openai.com/x"),
            ("manual", "https://www.reddit.com/r/x/1"),
        ]
        self.assertEqual(
            assess_evidence_gaps(facts),
            ("early_signal", "personal_testing"),
        )

    def test_personal_testing_always_listed(self):
        self.assertIn("personal_testing", ALL_GAPS)


if __name__ == "__main__":
    unittest.main()
