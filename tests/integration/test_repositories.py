"""Integration tests for the v1 repositories."""

import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.domain import models as m  # noqa: E402
from ai_signal.domain import states as st  # noqa: E402
from ai_signal.storage import sqlite as S  # noqa: E402
from ai_signal.storage.repositories import (  # noqa: E402
    CollectionRunRepository,
    RawSignalRepository,
    SignalRepository,
    StateTransitionRepository,
)

AWARE = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)


class RepositoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai_signal_repo_")
        self.db = os.path.join(self.tmp, "test.db")
        S.initialize_database(self.db)
        self.conn = S._open(self.db)

    def tearDown(self):
        try:
            self.conn.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_collection_run_insert_get_update(self):
        repo = CollectionRunRepository(self.conn)
        run = m.CollectionRun(week_key="2026-W33", started_at=AWARE, config_snapshot={"mode": "shadow"})
        repo.insert(run)

        fetched = repo.get(run.id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.week_key, "2026-W33")
        self.assertEqual(fetched.config_snapshot, {"mode": "shadow"})
        self.assertEqual(fetched.status, "running")

        repo.update_status(run.id, "success", finished_at=AWARE)
        finished = repo.get(run.id)
        self.assertEqual(finished.status, "success")
        self.assertEqual(finished.finished_at, AWARE)

    def test_collection_run_update_unknown_raises(self):
        repo = CollectionRunRepository(self.conn)
        with self.assertRaises(S.StorageError):
            repo.update_status("does-not-exist", "success", finished_at=AWARE)

    def test_raw_signal_idempotent(self):
        run = m.CollectionRun(week_key="2026-W33", started_at=AWARE, config_snapshot={})
        CollectionRunRepository(self.conn).insert(run)

        repo = RawSignalRepository(self.conn)
        raw = m.RawSignal(
            collection_run_id=run.id,
            source="news",
            external_id="ext-1",
            payload={"title": "x"},
            payload_sha256="hash-1",
            collected_at=AWARE,
            source_version="fixture-v1",
        )
        first = repo.upsert(raw)
        second = repo.upsert(raw)
        self.assertEqual(first.id, second.id)
        self.assertEqual(first.source_version, "fixture-v1")
        count = self.conn.execute("SELECT COUNT(*) FROM raw_signal").fetchone()[0]
        self.assertEqual(count, 1)

    def test_signal_idempotent(self):
        run = m.CollectionRun(week_key="2026-W33", started_at=AWARE, config_snapshot={})
        CollectionRunRepository(self.conn).insert(run)
        raw_repo = RawSignalRepository(self.conn)
        raw = raw_repo.upsert(
            m.RawSignal(
                collection_run_id=run.id,
                source="news",
                external_id="ext-1",
                payload={},
                payload_sha256="hash-1",
                collected_at=AWARE,
                source_version="fixture-v1",
            )
        )
        signal_repo = SignalRepository(self.conn)
        signal = m.Signal(
            collection_run_id=run.id,
            source="news",
            canonical_key="key-1",
            raw_signal_id=raw.id,
            first_seen_at=AWARE,
            signal_type="news",
        )
        first = signal_repo.upsert(signal)
        second = signal_repo.upsert(signal)
        self.assertEqual(first.id, second.id)
        count = self.conn.execute("SELECT COUNT(*) FROM signal").fetchone()[0]
        self.assertEqual(count, 1)

    def test_foreign_key_failure_propagated(self):
        repo = RawSignalRepository(self.conn)
        raw = m.RawSignal(
            collection_run_id="nonexistent-run",
            source="news",
            external_id="ext-1",
            payload={},
            payload_sha256="hash-1",
            collected_at=AWARE,
        )
        with self.assertRaises(S.StorageError):
            repo.upsert(raw)

    def test_state_transition_audit_appended(self):
        repo = StateTransitionRepository(self.conn)
        repo.append(st.transition("sig-1", "collected", "normalized"))
        repo.append(st.transition("sig-1", "normalized", "clustered"))

        records = repo.list_for("sig-1")
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].to_state, "normalized")
        self.assertEqual(records[1].to_state, "clustered")

    def test_transaction_rollback_leaves_no_half_written_rows(self):
        repo = CollectionRunRepository(self.conn)
        self.conn.execute("BEGIN")
        repo.insert(m.CollectionRun(week_key="2026-W33", started_at=AWARE, config_snapshot={}))
        # A caller-initiated rollback must undo the insert.
        self.conn.execute("ROLLBACK")
        count = self.conn.execute("SELECT COUNT(*) FROM collection_run").fetchone()[0]
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
