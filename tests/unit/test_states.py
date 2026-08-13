"""Unit tests for :mod:`ai_signal.domain.states`."""

import pathlib
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.domain import states as st  # noqa: E402


class LegalTransitionsTest(unittest.TestCase):
    def test_full_happy_path(self):
        entity = "sig-1"
        record = st.transition(entity, "collected", "normalized")
        self.assertEqual(record.to_state, "normalized")
        st.transition(entity, "normalized", "clustered")
        st.transition(entity, "clustered", "verified")
        st.transition(entity, "verified", "packaged")
        st.transition(entity, "packaged", "adopted")
        st.transition(entity, "adopted", "published")
        st.transition(entity, "published", "measured")

    def test_insufficient_evidence_branch(self):
        st.transition("e", "clustered", "insufficient_evidence")
        st.transition("e", "insufficient_evidence", "packaged")

    def test_packaged_decision_branches(self):
        st.transition("a", "packaged", "adopted")
        st.transition("b", "packaged", "parked")
        st.transition("c", "packaged", "rejected")


class IllegalTransitionsTest(unittest.TestCase):
    def test_skipping_ahead_is_illegal(self):
        with self.assertRaises(st.InvalidStateTransition):
            st.transition("e", "collected", "published")

    def test_backward_is_illegal(self):
        with self.assertRaises(st.InvalidStateTransition):
            st.transition("e", "verified", "collected")

    def test_unknown_state_is_illegal(self):
        with self.assertRaises(st.InvalidStateTransition):
            st.transition("e", "collected", "invented")


class FailureAndTerminalTest(unittest.TestCase):
    def test_failed_reachable_from_business_states(self):
        st.transition("e", "collected", "failed")
        st.transition("e", "normalized", "quarantined")
        st.transition("e", "published", "failed")

    def test_terminal_states_have_no_recovery(self):
        for terminal in ("measured", "parked", "rejected", "failed", "quarantined"):
            self.assertTrue(st.is_terminal(terminal))
            self.assertFalse(st.can_transition(terminal, "collected"))
            self.assertFalse(st.can_transition(terminal, "measured"))

    def test_can_transition_predicate(self):
        self.assertTrue(st.can_transition("collected", "normalized"))
        self.assertFalse(st.can_transition("measured", "collected"))


class RecordShapeTest(unittest.TestCase):
    def test_record_has_all_fields(self):
        record = st.transition(
            "sig-9",
            "collected",
            "normalized",
            reason="normalized ok",
            stage="normalize",
            policy_version_id="policy-1",
        )
        for field in (
            "entity_id",
            "from_state",
            "to_state",
            "timestamp",
            "reason",
            "stage",
            "policy_version_id",
        ):
            self.assertTrue(hasattr(record, field))
        self.assertEqual(record.entity_id, "sig-9")
        self.assertEqual(record.from_state, "collected")
        self.assertEqual(record.policy_version_id, "policy-1")

    def test_record_timestamp_defaults_to_aware_utc(self):
        record = st.transition("e", "collected", "normalized")
        self.assertIsNotNone(record.timestamp.tzinfo)
        self.assertEqual(record.timestamp.tzinfo, timezone.utc)

    def test_record_accepts_explicit_timestamp(self):
        when = datetime(2026, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
        record = st.transition("e", "collected", "normalized", timestamp=when)
        self.assertEqual(record.timestamp, when)


if __name__ == "__main__":
    unittest.main()
