"""Unit tests for the phase 2C1 candidate domain models (identity v2)."""

import pathlib
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.domain.models import (  # noqa: E402
    CANDIDATE_DECISIONS,
    CANDIDATE_LANES,
    CANDIDATE_TRIGGER_KINDS,
    Candidate,
    CandidateAssessment,
    CandidateDiscovery,
    candidate_assessment_entity_id,
    candidate_discovery_entity_id,
    candidate_entity_id,
)

TS = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc)
INPUT_HASH = "a" * 64


def _candidate(**overrides):
    defaults = dict(
        source="github",
        canonical_key="github:repository:90001",
        title="example-org/adhd-one-like",
        url="https://github.com/example-org/adhd-one-like",
        first_seen_at=TS,
    )
    defaults.update(overrides)
    return Candidate(**defaults)


def _discovery(**overrides):
    defaults = dict(
        candidate_id="c" * 64,
        week_key="2026-W33",
        scope_key="emerging-ai-agent-v1",
        lane="emerging",
        raw_signal_id="r" * 64,
        observed_at=TS,
    )
    defaults.update(overrides)
    return CandidateDiscovery(**defaults)


def _assessment(**overrides):
    defaults = dict(
        candidate_discovery_id="d" * 64,
        policy_version="candidate-gate-v2",
        input_hash=INPUT_HASH,
        decision="research",
        trigger_kind="repository_snapshot",
        trigger_summary="发现仓库快照，但尚未确认 Release、Launch 或重大变化。",
        reason_codes=("substantive_description", "agent_relevance_match"),
        missing_evidence=("specific_event", "readme"),
        attributes={"full_name": "org/repo"},
        assessed_at=TS,
    )
    defaults.update(overrides)
    return CandidateAssessment(**defaults)


class EnumsTest(unittest.TestCase):
    def test_controlled_vocabularies(self):
        self.assertEqual(
            CANDIDATE_LANES, ("watchlist", "mature", "emerging", "ecosystem")
        )
        self.assertEqual(CANDIDATE_DECISIONS, ("research", "watch", "reject"))
        self.assertEqual(CANDIDATE_TRIGGER_KINDS, ("repository_snapshot",))

    def test_publishability_words_not_in_vocab(self):
        joined = " ".join(CANDIDATE_DECISIONS)
        self.assertNotIn("ready_to_write", joined)
        self.assertNotIn("needs_testing", joined)


class CandidateModelTest(unittest.TestCase):
    def test_identity_is_source_plus_canonical_key_only(self):
        first = _candidate()
        second = _candidate()
        self.assertEqual(first.id, second.id)
        self.assertEqual(
            first.id,
            candidate_entity_id("github", "github:repository:90001"),
        )

    def test_same_repo_across_weeks_scopes_lanes_shares_identity(self):
        # The identity carries no week/scope/lane: discovery rows do.
        base = _candidate()
        self.assertEqual(base.id, candidate_entity_id("github", base.canonical_key))

    def test_rejects_invalid_inputs(self):
        for overrides in (
            dict(canonical_key=""),
            dict(title=""),
            dict(source="gitlab"),
            dict(url="http://github.com/x"),
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises((ValueError, TypeError)):
                    _candidate(**overrides)

    def test_naive_datetime_rejected(self):
        with self.assertRaises(ValueError):
            _candidate(first_seen_at=datetime(2026, 8, 15, 12, 0, 0))

    def test_first_seen_not_later_than_last_seen(self):
        earlier = datetime(2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc)
        with self.assertRaises(ValueError):
            _candidate(last_seen_at=earlier)

    def test_frozen(self):
        cand = _candidate()
        with self.assertRaises(Exception):
            cand.title = "x"  # type: ignore[misc]


class CandidateDiscoveryModelTest(unittest.TestCase):
    def test_discovery_context_changes_identity(self):
        base = _discovery()
        self.assertNotEqual(base.id, _discovery(lane="mature").id)
        self.assertNotEqual(base.id, _discovery(scope_key="other-scope-v1").id)
        self.assertNotEqual(base.id, _discovery(week_key="2026-W34").id)
        self.assertNotEqual(base.id, _discovery(raw_signal_id="s" * 64).id)

    def test_stable_id(self):
        self.assertEqual(_discovery().id, _discovery().id)
        self.assertEqual(
            _discovery().id,
            candidate_discovery_entity_id(
                "c" * 64, "2026-W33", "emerging-ai-agent-v1",
                "emerging", "r" * 64,
            ),
        )

    def test_rejects_invalid_inputs(self):
        for overrides in (
            dict(candidate_id=""),
            dict(week_key=""),
            dict(scope_key="UPPER"),
            dict(lane="hot"),
            dict(raw_signal_id=""),
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises((ValueError, TypeError)):
                    _discovery(**overrides)


class CandidateAssessmentModelTest(unittest.TestCase):
    def test_valid_assessment_and_stable_id(self):
        first = _assessment()
        self.assertEqual(first.id, _assessment().id)
        self.assertEqual(
            first.id,
            candidate_assessment_entity_id("d" * 64, INPUT_HASH, "candidate-gate-v2"),
        )

    def test_new_input_hash_is_new_revision(self):
        self.assertNotEqual(_assessment().id, _assessment(input_hash="b" * 64).id)

    def test_rejects_invalid_inputs(self):
        for overrides in (
            dict(decision="ready_to_write"),
            dict(decision="needs_testing"),
            dict(decision="publish"),
            dict(trigger_kind="editorial_decision"),
            dict(input_hash="not-hex"),
            dict(input_hash="a" * 63),
            dict(trigger_summary=""),
            dict(candidate_discovery_id=""),
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises((ValueError, TypeError)):
                    _assessment(**overrides)

    def test_sequences_become_tuples(self):
        assess = _assessment(reason_codes=["a", "b"], missing_evidence=["x", "y"])
        self.assertEqual(assess.reason_codes, ("a", "b"))
        self.assertEqual(assess.missing_evidence, ("x", "y"))

    def test_sensitive_attributes_rejected(self):
        from ai_signal.domain.models import SensitiveDataError

        with self.assertRaises(SensitiveDataError):
            _assessment(attributes={"token": "x"})
        with self.assertRaises(SensitiveDataError):
            _assessment(attributes={"nested": {"api_key": "x"}})

    def test_naive_datetime_rejected(self):
        with self.assertRaises(ValueError):
            _assessment(assessed_at=datetime(2026, 8, 15, 12, 0, 0))


if __name__ == "__main__":
    unittest.main()
