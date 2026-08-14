"""Unit tests for :mod:`ai_signal.pipeline.package` (validator + builders)."""

import pathlib
import sys
import unittest
from dataclasses import dataclass
from typing import Any, Mapping, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.pipeline.package import (  # noqa: E402
    PackageValidationError,
    _build_a,
    _build_b,
    _build_c,
    _build_d,
    _build_e,
    _build_f,
    validate_pack,
)


@dataclass
class _FakeEvidence:
    id: str
    source: str
    url: Optional[str]
    snippet: str
    payload: Mapping[str, Any]


def _ev(url="https://example.com/repo", snippet="example-repo", payload=None):
    return _FakeEvidence(
        id="ev1",
        source="github",
        url=url,
        snippet=snippet,
        payload=payload or {"stargazers_count": 42},
    )


class ValidatorTest(unittest.TestCase):
    def test_valid_pack_passes(self):
        ev = _ev(
            snippet="example-repo - updated 2026-08-11T00:00:00+00:00",
            payload={
                "html_url": "https://example.com/repo",
                "stargazers_count": 42,
            },
        )
        claims = {
            "c1": "Repository example-repo is publicly accessible at https://example.com/repo.",
            "c2": "The repository has 42 stargazers.",
        }
        validate_pack(claims, {"c1": [ev], "c2": [ev]})

    def test_claim_without_evidence_fails(self):
        with self.assertRaises(PackageValidationError):
            validate_pack({"c1": "x"}, {"c1": []})

    def test_unsafe_evidence_url_fails(self):
        ev = _ev(url="http://example.com/repo")
        with self.assertRaises(PackageValidationError):
            validate_pack({"c1": "x"}, {"c1": [ev]})

    def test_untraceable_number_fails(self):
        ev = _ev(snippet="has 10 stars", payload={"stargazers_count": 10})
        with self.assertRaises(PackageValidationError):
            validate_pack({"c1": "The repository has 999 stargazers."}, {"c1": [ev]})

    # ----- cross-claim counter-examples (Fix 4) ----- #

    def test_cross_claim_evidence_borrowing_rejected(self):
        # Claim A references 999, which only exists in Claim B's evidence.
        ev_b = _ev(payload={"stargazers_count": 999})
        with self.assertRaises(PackageValidationError):
            validate_pack(
                {"cA": "The repository has 999 stargazers."},
                {"cA": [_ev(payload={"stargazers_count": 1})]},
            )

    def test_date_day_part_similar_but_time_differs_rejected(self):
        ev = _ev(
            snippet="updated 2026-08-11T09:00:00+00:00",
            payload={"updated_at": "2026-08-11T09:00:00+00:00"},
        )
        with self.assertRaises(PackageValidationError):
            validate_pack(
                {"c1": "The repository was last updated at 2026-08-11T10:00:00+00:00."},
                {"c1": [ev]},
            )

    def test_url_only_in_other_claim_rejected(self):
        # Claim A's URL appears only in Claim B's evidence.
        ev_a = _ev(url="https://example.com/a", payload={"html_url": "https://example.com/a"})
        with self.assertRaises(PackageValidationError):
            validate_pack(
                {"cA": "Accessible at https://example.com/b."},
                {"cA": [ev_a]},
            )


class AngleBuildersTest(unittest.TestCase):
    def test_a_lists_claim_ids(self):
        a = _build_a(("c1", "c2"), ["fact1", "fact2"])
        self.assertEqual(a["claim_ids"], ["c1", "c2"])
        self.assertEqual(len(a["facts"]), 2)

    def test_b_has_unknowns_and_limitations(self):
        b = _build_b("ev1", "github", "https://example.com/repo", "snippet")
        self.assertIn("Single GitHub API snapshot", " ".join(b["unknowns"]))
        self.assertGreater(len(b["limitations"]), 0)

    def test_c_heat_unmeasured(self):
        c = _build_c({"stargazers_count": 10, "description": "x", "topics": ["a"]})
        self.assertEqual(c["heat_status"], "unmeasured")

    def test_d_is_editorial(self):
        self.assertTrue(_build_d()["editorial_hypothesis"])

    def test_e_has_safety_steps(self):
        steps = " ".join(_build_e()["steps"])
        self.assertIn("isolated", steps)
        self.assertIn("Do not provide credentials", steps)

    def test_f_is_editorial_outline(self):
        self.assertTrue(_build_f()["editorial_outline"])


if __name__ == "__main__":
    unittest.main()
