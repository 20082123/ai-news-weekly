"""Unit tests for :mod:`ai_signal.domain.models`."""

import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.domain import models as m  # noqa: E402

AWARE = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)
NAIVE = datetime(2026, 8, 13, 12, 0, 0)
CST = timezone(timedelta(hours=8))


class TimeHelperTest(unittest.TestCase):
    def test_aware_utc_passes_through(self):
        self.assertEqual(m.ensure_aware_utc(AWARE), AWARE)
        self.assertEqual(m.ensure_aware_utc(AWARE).tzinfo, timezone.utc)

    def test_other_timezone_converted_to_utc(self):
        local = datetime(2026, 8, 13, 20, 0, 0, tzinfo=CST)
        converted = m.ensure_aware_utc(local)
        self.assertEqual(converted, AWARE)
        self.assertEqual(converted.tzinfo, timezone.utc)

    def test_naive_rejected(self):
        with self.assertRaises(ValueError):
            m.ensure_aware_utc(NAIVE)

    def test_non_datetime_rejected(self):
        with self.assertRaises(TypeError):
            m.ensure_aware_utc("2026-08-13")  # type: ignore[arg-type]


class DeterministicIdTest(unittest.TestCase):
    def test_deterministic_id_is_stable(self):
        self.assertEqual(m.deterministic_id("a", "b"), m.deterministic_id("a", "b"))

    def test_deterministic_id_distinct_for_distinct_parts(self):
        self.assertNotEqual(m.deterministic_id("a", "b"), m.deterministic_id("ab", ""))

    def test_builder_helpers_are_stable(self):
        self.assertEqual(
            m.raw_signal_id("news", "ext1", "hash"),
            m.raw_signal_id("news", "ext1", "hash"),
        )
        self.assertEqual(
            m.signal_entity_id("news", "key"),
            m.signal_entity_id("news", "key"),
        )

    def test_run_ids_are_random(self):
        self.assertNotEqual(m.generate_run_id(), m.generate_run_id())


class ModelValidationTest(unittest.TestCase):
    def test_raw_signal_rejects_naive_datetime(self):
        with self.assertRaises(ValueError):
            m.RawSignal(
                collection_run_id="run-1",
                source="news",
                external_id="ext1",
                payload={"k": 1},
                payload_sha256="hash",
                collected_at=NAIVE,
            )

    def test_raw_signal_auto_derives_deterministic_id(self):
        kwargs = dict(
            collection_run_id="run-1",
            source="news",
            external_id="ext1",
            payload={"k": 1},
            payload_sha256="hash",
            collected_at=AWARE,
        )
        first = m.RawSignal(**kwargs)
        second = m.RawSignal(**kwargs)
        self.assertEqual(first.id, second.id)
        self.assertEqual(first.collected_at, AWARE)

    def test_raw_signal_normalizes_timezone(self):
        raw = m.RawSignal(
            collection_run_id="run-1",
            source="news",
            external_id="ext1",
            payload={},
            payload_sha256="hash",
            collected_at=datetime(2026, 8, 13, 20, 0, tzinfo=CST),
        )
        self.assertEqual(raw.collected_at, AWARE)

    def test_payload_must_be_mapping(self):
        with self.assertRaises(TypeError):
            m.SourceItem(external_id="e", collected_at=AWARE, payload=[1, 2, 3])  # type: ignore[arg-type]

    def test_payload_must_be_serializable(self):
        with self.assertRaises(TypeError):
            m.SourceItem(external_id="e", collected_at=AWARE, payload=object())  # type: ignore[arg-type]

    def test_payload_rejects_nested_credential_fields(self):
        with self.assertRaises(m.SensitiveDataError):
            m.SourceItem(
                external_id="e",
                collected_at=AWARE,
                payload={"request": {"headers": {"Authorization": "hidden"}}},
            )

    def test_payload_allows_non_sensitive_capability_flags(self):
        item = m.SourceItem(
            external_id="e",
            collected_at=AWARE,
            payload={"email_enabled": False, "token_count": 12},
        )
        self.assertEqual(item.payload["token_count"], 12)

    def test_source_batch_status_validated(self):
        m.SourceBatch(source="news", status="success", started_at=AWARE, finished_at=AWARE)
        with self.assertRaises(ValueError):
            m.SourceBatch(source="news", status="bogus", started_at=AWARE, finished_at=AWARE)

    def test_source_batch_coerces_sequences(self):
        item = m.SourceItem(external_id="e", collected_at=AWARE, payload={})
        batch = m.SourceBatch(
            source="news",
            status="partial",
            started_at=AWARE,
            finished_at=AWARE,
            items=[item],
            warnings=["slow"],
        )
        self.assertIsInstance(batch.items, tuple)
        self.assertEqual(len(batch.items), 1)
        self.assertIsInstance(batch.warnings, tuple)

    def test_source_batch_rejects_reversed_time_range(self):
        with self.assertRaises(ValueError):
            m.SourceBatch(
                source="news",
                status="success",
                started_at=AWARE,
                finished_at=AWARE - timedelta(seconds=1),
            )

    def test_signal_state_validated(self):
        with self.assertRaises(ValueError):
            m.Signal(
                collection_run_id="run",
                source="news",
                canonical_key="key",
                raw_signal_id="raw",
                first_seen_at=AWARE,
                signal_type="news",
                state="invented",
            )

    def test_feedback_decision_validated(self):
        with self.assertRaises(ValueError):
            m.Feedback(target_type="signal", target_id="x", decision="bogus", created_at=AWARE)

    def test_feedback_usefulness_range(self):
        m.Feedback(
            target_type="signal", target_id="x", decision="adopted",
            created_at=AWARE, usefulness=3,
        )
        with self.assertRaises(ValueError):
            m.Feedback(
                target_type="signal", target_id="x", decision="adopted",
                created_at=AWARE, usefulness=0,
            )
        with self.assertRaises(ValueError):
            m.Feedback(
                target_type="signal", target_id="x", decision="adopted",
                created_at=AWARE, usefulness=9,
            )
        with self.assertRaises(TypeError):
            m.Feedback(
                target_type="signal", target_id="x", decision="adopted",
                created_at=AWARE, usefulness=2.5,
            )

    def test_claim_evidence_weight_range(self):
        m.ClaimEvidence(claim_id="c", evidence_id="e", created_at=AWARE, weight=0.5)
        with self.assertRaises(ValueError):
            m.ClaimEvidence(claim_id="c", evidence_id="e", created_at=AWARE, weight=2.0)

    def test_claim_evidence_relation_validated(self):
        with self.assertRaises(ValueError):
            m.ClaimEvidence(
                claim_id="c", evidence_id="e", created_at=AWARE, relation="invented"
            )

    def test_evidence_computes_payload_sha256(self):
        evidence = m.Evidence(
            source="news", snippet="hello world", collected_at=AWARE, payload={"a": 1}
        )
        self.assertEqual(len(evidence.payload_sha256), 64)

    def test_models_are_frozen(self):
        raw = m.RawSignal(
            collection_run_id="r", source="s", external_id="e",
            payload={}, payload_sha256="h", collected_at=AWARE,
        )
        with self.assertRaises(Exception):
            raw.source = "other"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
