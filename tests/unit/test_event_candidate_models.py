"""Unit tests for the phase 2C3 event-candidate domain models."""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.domain.models import (  # noqa: E402
    EVENT_CANDIDATE_SOURCE_KINDS,
    SIGNAL_TYPES,
    EventCandidate,
    EventCandidateSourceRef,
    event_candidate_entity_id,
    event_candidate_source_ref_entity_id,
)


def _candidate(**overrides):
    fields = {
        "signal_type": "economics_access",
        "subject": "DeepSeek V4 API",
        "change_summary": "峰谷计价生效",
        "affected_audience": "API 开发者",
        "work_impact_hypothesis": "成本重算",
        "research_priority": 90,
        "missing_evidence": ("third_party_cloud_sync",),
    }
    fields.update(overrides)
    return EventCandidate(**fields)


class SignalTaxonomyTest(unittest.TestCase):
    def test_five_global_signal_types(self):
        self.assertEqual(
            SIGNAL_TYPES,
            (
                "capability_change",
                "tool_workflow_change",
                "user_reality",
                "economics_access",
                "ecosystem_market_shift",
            ),
        )

    def test_source_kinds(self):
        self.assertEqual(
            EVENT_CANDIDATE_SOURCE_KINDS,
            ("github_repository_candidate", "official_announcement_candidate"),
        )


class EventCandidateModelTest(unittest.TestCase):
    def test_identity_deterministic(self):
        a = _candidate()
        b = _candidate()
        self.assertEqual(a.id, b.id)
        self.assertEqual(len(a.id), 64)
        int(a.id, 16)

    def test_identity_covers_type_subject_change(self):
        base = _candidate()
        self.assertNotEqual(
            base.id, _candidate(signal_type="capability_change").id
        )
        self.assertNotEqual(base.id, _candidate(subject="别的主题").id)
        self.assertNotEqual(base.id, _candidate(change_summary="别的变化").id)

    def test_invalid_signal_type_rejected(self):
        with self.assertRaises(ValueError):
            _candidate(signal_type="repo_star_growth")

    def test_empty_fields_rejected(self):
        for field in (
            "subject",
            "change_summary",
            "affected_audience",
            "work_impact_hypothesis",
        ):
            with self.assertRaises(ValueError):
                _candidate(**{field: "  "})

    def test_priority_bounds(self):
        for bad in (-1, 101, True, "90"):
            with self.assertRaises((ValueError, TypeError)):
                _candidate(research_priority=bad)
        self.assertEqual(_candidate(research_priority=0).research_priority, 0)
        self.assertEqual(_candidate(research_priority=100).research_priority, 100)

    def test_missing_evidence_normalized(self):
        self.assertEqual(_candidate(missing_evidence=None).missing_evidence, ())
        self.assertEqual(
            _candidate(missing_evidence=["a", "b"]).missing_evidence, ("a", "b")
        )

    def test_ref_model_and_identity(self):
        candidate = _candidate()
        ref = EventCandidateSourceRef(
            event_candidate_id=candidate.id,
            source_kind="github_repository_candidate",
            ref_id="cand-abc",
            ref_label="example-org/repo",
        )
        self.assertEqual(
            ref.id,
            event_candidate_source_ref_entity_id(
                candidate.id, "github_repository_candidate", "cand-abc"
            ),
        )
        with self.assertRaises(ValueError):
            EventCandidateSourceRef(
                event_candidate_id=candidate.id,
                source_kind="reddit_thread",
                ref_id="x",
                ref_label="x",
            )
        with self.assertRaises(ValueError):
            EventCandidateSourceRef(
                event_candidate_id=candidate.id,
                source_kind="official_announcement_candidate",
                ref_id="",
                ref_label="x",
            )


if __name__ == "__main__":
    unittest.main()
