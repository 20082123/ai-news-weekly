"""Unit tests for :mod:`ai_signal.pipeline.package` (validator + builders)."""

import pathlib
import sys
import unittest
from dataclasses import dataclass
from typing import Any, Mapping, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.pipeline.package import (  # noqa: E402
    SCHEMA_VERSION,
    PackageValidationError,
    _build_a,
    _build_b,
    _build_c,
    _build_d,
    _build_e,
    _build_f,
    _build_project_info,
    validate_pack,
)


@dataclass
class _FakeEvidence:
    id: str
    source: str
    url: Optional[str]
    snippet: str
    payload: Mapping[str, Any]


def _ev(url="https://github.com/org/repo", snippet="org/repo", payload=None):
    return _FakeEvidence(
        id="ev1",
        source="github",
        url=url,
        snippet=snippet,
        payload=payload or {"stargazers_count": 42},
    )


class ValidatorTest(unittest.TestCase):
    def test_valid_chinese_claims_pass(self):
        ev = _ev(
            snippet="org/repo - updated 2026-08-11T00:00:00+00:00",
            payload={
                "html_url": "https://github.com/org/repo",
                "stargazers_count": 42,
                "forks_count": 7,
                "description": "an example repository",
                "topics": ["ai", "agents"],
            },
        )
        claims = {
            "c1": "仓库 org/repo 可通过 https://github.com/org/repo 公开访问。",
            "c2": "最近更新时间为 2026-08-11T00:00:00+00:00。",
            "c3": "GitHub 当前快照显示该仓库有 42 个 stars、7 个 forks。",
            "c4": "仓库简介为：an example repository。",
            "c5": "仓库 topics 包括：ai、agents。",
        }
        validate_pack(claims, {cid: [ev] for cid in claims})

    def test_claim_without_evidence_fails(self):
        with self.assertRaises(PackageValidationError):
            validate_pack({"c1": "x"}, {"c1": []})

    def test_unsafe_evidence_url_fails(self):
        ev = _ev(url="http://github.com/org/repo")
        with self.assertRaises(PackageValidationError):
            validate_pack({"c1": "x"}, {"c1": [ev]})

    def test_untraceable_number_fails(self):
        ev = _ev(snippet="has 10 stars", payload={"stargazers_count": 10})
        with self.assertRaises(PackageValidationError):
            validate_pack({"c1": "GitHub 当前快照显示该仓库有 999 个 stars。"}, {"c1": [ev]})

    def test_untraceable_description_number_fails(self):
        # A description quoting numbers not present in the evidence fails.
        ev = _ev(payload={"description": "version two"})
        with self.assertRaises(PackageValidationError):
            validate_pack({"c1": "仓库简介为：version 99。"}, {"c1": [ev]})

    # ----- cross-claim counter-examples ----- #

    def test_cross_claim_evidence_borrowing_rejected(self):
        ev_a = _ev(payload={"stargazers_count": 1})
        with self.assertRaises(PackageValidationError):
            validate_pack(
                {"cA": "GitHub 当前快照显示该仓库有 999 个 stars。"},
                {"cA": [ev_a]},
            )

    def test_date_time_differs_rejected(self):
        ev = _ev(
            snippet="updated 2026-08-11T09:00:00+00:00",
            payload={"updated_at": "2026-08-11T09:00:00+00:00"},
        )
        with self.assertRaises(PackageValidationError):
            validate_pack(
                {"c1": "最近更新时间为 2026-08-11T10:00:00+00:00。"},
                {"c1": [ev]},
            )

    def test_url_only_in_other_claim_rejected(self):
        ev_a = _ev(url="https://github.com/a", payload={"html_url": "https://github.com/a"})
        with self.assertRaises(PackageValidationError):
            validate_pack(
                {"cA": "仓库 org/repo 可通过 https://github.com/b 公开访问。"},
                {"cA": [ev_a]},
            )


class AngleBuildersTest(unittest.TestCase):
    _REPO = {
        "full_name": "org/repo",
        "description": "an example repository",
        "topics": ["ai", "agents"],
        "language": "Python",
        "stargazers_count": 42,
        "forks_count": 7,
    }

    def test_schema_version_is_v2(self):
        self.assertEqual(SCHEMA_VERSION, "material-pack-v2")

    def test_a_lists_claim_ids(self):
        a = _build_a(("c1", "c2"), ["事实一", "事实二"])
        self.assertEqual(a["claim_ids"], ["c1", "c2"])
        self.assertEqual(len(a["facts"]), 2)

    def test_b_chinese_unknowns_and_limitations(self):
        b = _build_b("ev1", "github", "https://github.com/org/repo", "snippet")
        joined = " ".join(b["unknowns"]) + " ".join(b["limitations"])
        self.assertIn("无法据此证明任何趋势或增长", joined)
        self.assertIn("不代表增长速度", joined)

    def test_c_heat_unmeasured_notes(self):
        c = _build_c(self._REPO)
        self.assertEqual(c["heat_status"], "unmeasured")
        joined = " ".join(c["notes"])
        self.assertIn("尚未测量", joined)
        self.assertIn("历史快照", joined)
        self.assertNotIn("升温了", joined)

    def test_d_references_repo_fields(self):
        d = _build_d(self._REPO)
        self.assertTrue(d["editorial_hypothesis"])
        notes = " ".join(a["note"] for a in d["angles"])
        self.assertIn("org/repo", notes)
        self.assertIn("ai", notes)  # topics quoted

    def test_e_references_repo_and_safety_steps(self):
        e = _build_e(self._REPO)
        steps = " ".join(e["steps"])
        self.assertIn("org/repo", steps)
        self.assertIn("隔离环境", steps)
        self.assertIn("不提供任何凭据", steps)

    def test_f_references_repo_fields(self):
        f = _build_f(self._REPO)
        self.assertTrue(f["editorial_outline"])
        blob = f["bilibili_outline"] + " ".join(f["xiaohongshu_points"]) + f["douyin_hook"]
        self.assertIn("org/repo", blob)
        self.assertNotIn("爆火", blob)
        self.assertNotIn("行业领先", blob)

    def test_project_info_from_payload_only(self):
        info = _build_project_info(self._REPO)
        self.assertEqual(info["full_name"], "org/repo")
        self.assertEqual(info["description"], "an example repository")
        self.assertEqual(info["topics"], ["ai", "agents"])
        self.assertEqual(info["stargazers_count"], 42)
        # Missing fields become None, never fabricated.
        info2 = _build_project_info({"full_name": "x/y"})
        self.assertIsNone(info2["description"])
        self.assertEqual(info2["topics"], [])


if __name__ == "__main__":
    unittest.main()
