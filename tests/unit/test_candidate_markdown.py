"""Unit tests for :mod:`ai_signal.outputs.candidate_markdown` (debug cards)."""

import pathlib
import re
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.domain.models import (  # noqa: E402
    Candidate,
    CandidateAssessment,
    CandidateDiscovery,
)
from ai_signal.outputs.candidate_markdown import (  # noqa: E402
    CandidateMarkdownError,
    _parse_frontmatter,
    build_candidate_filename,
    publish_candidate_markdown,
)
from ai_signal.pipeline.qualify import MISSING_EVIDENCE, QualifiedCandidate  # noqa: E402

TS = datetime(2026, 8, 15, tzinfo=timezone.utc)
_HEX = re.compile(r"[0-9a-f]{64}")


def _qc(decision="research", homepage_present=False):
    candidate = Candidate(
        source="github",
        canonical_key="github:repository:90001",
        title="example-org/adhd-one-like",
        url="https://example.com/example-org/adhd-one-like",
        first_seen_at=TS,
    )
    discovery = CandidateDiscovery(
        candidate_id=candidate.id,
        week_key="2026-W33",
        scope_key="emerging-ai-agent-v1",
        lane="emerging",
        raw_signal_id="r" * 64,
        observed_at=TS,
    )
    attributes = {
        "id": "90001",
        "full_name": "example-org/adhd-one-like",
        "html_url": "https://example.com/example-org/adhd-one-like",
        "description": "A Windows desktop application that integrates multiple AI models as a personal harness.",
        "topics": ["windows", "desktop", "ai-harness"],
        "language": "C#",
        "stargazers_count": 1,
        "forks_count": 0,
        "created_at": "2026-06-01T00:00:00+00:00",
        "updated_at": "2026-08-10T00:00:00+00:00",
        "pushed_at": "2026-08-10T00:00:00+00:00",
        "homepage_present": homepage_present,
    }
    assessment = CandidateAssessment(
        candidate_discovery_id=discovery.id,
        policy_version="candidate-gate-v2",
        input_hash="a" * 64,
        decision=decision,
        trigger_kind="repository_snapshot",
        trigger_summary="发现仓库快照，但尚未确认 Release、Launch 或重大变化。",
        reason_codes=("substantive_description", "agent_relevance_match"),
        missing_evidence=MISSING_EVIDENCE,
        attributes=attributes,
        assessed_at=TS,
    )
    return QualifiedCandidate(
        candidate=candidate, discovery=discovery, assessment=assessment
    )


class FilenameTest(unittest.TestCase):
    def test_readable_candidate_filename(self):
        name, needs_suffix = build_candidate_filename(
            "2026-W33", "example-org/adhd-one-like", "a" * 64
        )
        self.assertEqual(name, "2026-W33 - 候选 - adhd-one-like (example-org).md")
        self.assertFalse(needs_suffix)

    def test_illegal_chars_sanitized(self):
        name, _ = build_candidate_filename("2026-W33", 'bad<>:"/\\|?*/worse', "a" * 64)
        for ch in '<>:"/\\|?*':
            self.assertNotIn(ch, name)

    def test_reserved_device_name_gets_suffix(self):
        name, needs_suffix = build_candidate_filename("2026-W33", "owner/CON", "e" * 64)
        self.assertTrue(needs_suffix)

    def test_overlong_name_truncated_with_suffix(self):
        name, needs_suffix = build_candidate_filename(
            "2026-W33", "owner/" + "r" * 300, "e" * 64
        )
        self.assertTrue(needs_suffix)
        self.assertLessEqual(len(name), 120)


class PublishTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai_cand_md_")
        self.root = pathlib.Path(self.tmp)

    def _files(self):
        return sorted(self.root.glob("Candidates/**/*.md"))

    def test_research_written_to_research_dir_only(self):
        created = publish_candidate_markdown(self.root, _qc("research"))
        self.assertTrue(created)
        files = self._files()
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].parent.name, "Research")

    def test_watch_and_reject_never_render(self):
        with self.assertRaises(CandidateMarkdownError):
            publish_candidate_markdown(self.root, _qc("watch"))
        with self.assertRaises(CandidateMarkdownError):
            publish_candidate_markdown(self.root, _qc("reject"))
        self.assertEqual(self._files(), [])
        # Candidates/Watch is never created.
        self.assertFalse((self.root / "Candidates" / "Watch").exists())

    def test_chinese_sections_and_debug_disclaimer(self):
        publish_candidate_markdown(self.root, _qc("research"))
        text = self._files()[0].read_text(encoding="utf-8")
        self.assertIn("这是候选卡（调试/研究队列），不是可发布素材。", text)
        for heading in (
            "## 1. 候选状态",
            "## 2. 发现来源与 Lane",
            "## 3. 当前触发",
            "## 4. 为什么可能值得继续研究",
            "## 5. 当前已有的仓库元数据",
            "## 6. 缺失证据",
            "## 7. 下一步建议",
        ):
            self.assertIn(heading, text)
        self.assertIn("A Windows desktop application", text)
        self.assertIn("windows、desktop、ai-harness", text)

    def test_homepage_url_never_rendered(self):
        qc = _qc("research", homepage_present=True)
        publish_candidate_markdown(self.root, qc)
        text = self._files()[0].read_text(encoding="utf-8")
        self.assertIn("已填写（未核验）", text)
        self.assertNotIn("https://external", text)
        self.assertNotIn("http://", text.replace("https://", ""))

    def test_forbidden_wording_absent(self):
        publish_candidate_markdown(self.root, _qc("research"))
        text = self._files()[0].read_text(encoding="utf-8")
        for forbidden in (
            "A — 发生了什么", "建议发布", "可以直接写",
            "READY_TO_WRITE", "NEEDS_TESTING",
            "爆火", "快速增长", "行业领先", "升温了",
        ):
            self.assertNotIn(forbidden, text)

    def test_no_64hex_ids_in_visible_body(self):
        publish_candidate_markdown(self.root, _qc("research"))
        raw = self._files()[0].read_text(encoding="utf-8")
        fm = _parse_frontmatter(raw)
        self.assertIsNotNone(fm)
        body = raw.split("---\n", 2)[2]
        self.assertIsNone(_HEX.search(body))
        # ids live in frontmatter instead.
        self.assertEqual(fm["type"], "ai-signal-candidate")
        self.assertEqual(fm["candidate_id"], _qc().candidate.id)
        self.assertEqual(fm["discovery_id"], _qc().discovery.id)
        self.assertEqual(fm["qualification_decision"], "research")
        self.assertEqual(fm["qualification_policy"], "candidate-gate-v2")

    def test_missing_evidence_rendered(self):
        publish_candidate_markdown(self.root, _qc("research"))
        text = self._files()[0].read_text(encoding="utf-8")
        for item in MISSING_EVIDENCE:
            self.assertIn("- %s" % item, text)

    def test_rerun_updates_same_single_file(self):
        publish_candidate_markdown(self.root, _qc("research"))
        publish_candidate_markdown(self.root, _qc("research"))
        self.assertEqual(len(self._files()), 1)

    def test_new_assessment_same_candidate_week_in_place(self):
        publish_candidate_markdown(self.root, _qc("research"))
        qc = _qc("research")
        assessment2 = CandidateAssessment(
            candidate_discovery_id=qc.discovery.id,
            policy_version="candidate-gate-v2",
            input_hash="b" * 64,
            decision="research",
            trigger_kind="repository_snapshot",
            trigger_summary="发现仓库快照，但尚未确认 Release、Launch 或重大变化。",
            reason_codes=("substantive_description", "agent_relevance_match"),
            missing_evidence=MISSING_EVIDENCE,
            attributes=qc.assessment.attributes,
            assessed_at=TS,
        )
        qc_new = QualifiedCandidate(
            candidate=qc.candidate, discovery=qc.discovery, assessment=assessment2
        )
        created = publish_candidate_markdown(self.root, qc_new)
        self.assertFalse(created)
        self.assertEqual(len(self._files()), 1)
        fm = _parse_frontmatter(self._files()[0].read_text(encoding="utf-8"))
        self.assertEqual(fm["assessment_id"], assessment2.id)

    def test_same_candidate_other_week_writes_second_file(self):
        # Same candidate discovered in another week: a separate debug card.
        publish_candidate_markdown(self.root, _qc("research"))
        qc = _qc("research")
        discovery2 = CandidateDiscovery(
            candidate_id=qc.candidate.id,
            week_key="2026-W34",
            scope_key="emerging-ai-agent-v1",
            lane="emerging",
            raw_signal_id="r" * 64,
            observed_at=TS,
        )
        qc2 = QualifiedCandidate(
            candidate=qc.candidate,
            discovery=discovery2,
            assessment=CandidateAssessment(
                candidate_discovery_id=discovery2.id,
                policy_version="candidate-gate-v2",
                input_hash="c" * 64,
                decision="research",
                trigger_kind="repository_snapshot",
                trigger_summary="发现仓库快照，但尚未确认 Release、Launch 或重大变化。",
                reason_codes=("substantive_description",),
                missing_evidence=MISSING_EVIDENCE,
                attributes=qc.assessment.attributes,
                assessed_at=TS,
            ),
        )
        publish_candidate_markdown(self.root, qc2)
        self.assertEqual(len(self._files()), 2)

    def test_collision_with_other_candidate_uses_suffix(self):
        publish_candidate_markdown(self.root, _qc("research"))
        other_candidate = Candidate(
            source="github",
            canonical_key="github:repository:90002",
            title="example-org/adhd-one-like",
            url="https://example.com/example-org/adhd-one-like",
            first_seen_at=TS,
        )
        base = _qc("research")
        other = QualifiedCandidate(
            candidate=other_candidate,
            discovery=CandidateDiscovery(
                candidate_id=other_candidate.id,
                week_key="2026-W33",
                scope_key="emerging-ai-agent-v1",
                lane="emerging",
                raw_signal_id="s" * 64,
                observed_at=TS,
            ),
            assessment=CandidateAssessment(
                candidate_discovery_id=CandidateDiscovery(
                    candidate_id=other_candidate.id,
                    week_key="2026-W33",
                    scope_key="emerging-ai-agent-v1",
                    lane="emerging",
                    raw_signal_id="s" * 64,
                    observed_at=TS,
                ).id,
                policy_version="candidate-gate-v2",
                input_hash="d" * 64,
                decision="research",
                trigger_kind="repository_snapshot",
                trigger_summary="发现仓库快照，但尚未确认 Release、Launch 或重大变化。",
                reason_codes=("substantive_description",),
                missing_evidence=MISSING_EVIDENCE,
                attributes=base.assessment.attributes,
                assessed_at=TS,
            ),
        )
        publish_candidate_markdown(self.root, other)
        self.assertEqual(len(self._files()), 2)

    def test_invalid_existing_frontmatter_refused(self):
        target_dir = self.root / "Candidates" / "Research"
        target_dir.mkdir(parents=True)
        (target_dir / "2026-W33 - 候选 - adhd-one-like (example-org).md").write_text(
            "garbage", encoding="utf-8"
        )
        with self.assertRaises(CandidateMarkdownError):
            publish_candidate_markdown(self.root, _qc("research"))

    def test_traversal_title_stays_inside(self):
        qc = _qc("research")
        poisoned = Candidate(
            source="github",
            canonical_key="github:repository:90003",
            title="../../evil/repo",
            url="https://example.com/example-org/evil",
            first_seen_at=TS,
        )
        qc_evil = QualifiedCandidate(
            candidate=poisoned,
            discovery=CandidateDiscovery(
                candidate_id=poisoned.id,
                week_key="2026-W33",
                scope_key="emerging-ai-agent-v1",
                lane="emerging",
                raw_signal_id="r" * 64,
                observed_at=TS,
            ),
            assessment=qc.assessment,
        )
        publish_candidate_markdown(self.root, qc_evil)
        files = self._files()
        self.assertEqual(len(files), 1)
        self.assertEqual(
            files[0].resolve().parent,
            (self.root / "Candidates" / "Research").resolve(),
        )

    def test_html_in_body_escaped(self):
        qc = _qc("research")
        poisoned_attrs = dict(qc.assessment.attributes)
        poisoned_attrs["description"] = "<script>alert(1)</script>"
        assessment = CandidateAssessment(
            candidate_discovery_id=qc.discovery.id,
            policy_version="candidate-gate-v2",
            input_hash="e" * 64,
            decision="research",
            trigger_kind="repository_snapshot",
            trigger_summary="发现仓库快照，但尚未确认 Release、Launch 或重大变化。",
            reason_codes=("substantive_description",),
            missing_evidence=MISSING_EVIDENCE,
            attributes=poisoned_attrs,
            assessed_at=TS,
        )
        publish_candidate_markdown(
            self.root,
            QualifiedCandidate(
                candidate=qc.candidate,
                discovery=qc.discovery,
                assessment=assessment,
            ),
        )
        text = self._files()[0].read_text(encoding="utf-8")
        self.assertNotIn("<script>", text)

    def test_symlinked_dir_rejected(self):
        import os

        base = self.root / "Candidates"
        base.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        try:
            os.symlink(outside, base / "Research")
        except (OSError, NotImplementedError):
            self.skipTest("symlink not supported")
        with self.assertRaises(CandidateMarkdownError):
            publish_candidate_markdown(self.root, _qc("research"))


if __name__ == "__main__":
    unittest.main()
