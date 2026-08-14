"""Repositories for the v1 schema.

Phase 1 implements only the repositories needed for the collection and
state-audit path:

* :class:`CollectionRunRepository`
* :class:`RawSignalRepository`
* :class:`SignalRepository`
* :class:`StateTransitionRepository`

Rules:
* all SQL is parameterized;
* repositories never call ``COMMIT`` themselves - the caller owns the
  transaction, so a caller-initiated rollback never leaves half-written
  rows behind;
* duplicate ``raw_signal`` / ``signal`` writes return the existing record
  instead of creating a duplicate row (idempotent upserts);
* raw records are append-only: nothing here overwrites or deletes them.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import List, Optional

from ..domain.models import (
    CollectionRun,
    RawSignal,
    Signal,
    ensure_aware_utc,
    now_utc,
)
from ..domain.states import StateTransitionRecord
from .sqlite import StorageError


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    return ensure_aware_utc(value).isoformat()


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _wrap(exc: Exception) -> StorageError:
    return StorageError(str(exc))


class CollectionRunRepository:
    def __init__(self, conn):
        self.conn = conn

    def insert(self, run: CollectionRun) -> CollectionRun:
        try:
            self.conn.execute(
                "INSERT INTO collection_run "
                "(id, week_key, started_at, finished_at, status, config_snapshot, "
                " policy_version_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run.id,
                    run.week_key,
                    _iso(run.started_at),
                    _iso(run.finished_at),
                    run.status,
                    json.dumps(run.config_snapshot, ensure_ascii=False),
                    run.policy_version_id,
                    _iso(run.created_at),
                ),
            )
        except Exception as exc:  # noqa: BLE001 - surface as StorageError
            raise _wrap(exc) from exc
        return run

    def get(self, run_id: str) -> Optional[CollectionRun]:
        row = self.conn.execute(
            "SELECT * FROM collection_run WHERE id = ?", (run_id,)
        ).fetchone()
        return self._from_row(row) if row else None

    def update_status(
        self,
        run_id: str,
        status: str,
        finished_at: Optional[datetime] = None,
    ) -> None:
        if status not in ("running", "success", "partial", "failed"):
            raise ValueError("invalid collection run status: %r" % status)
        try:
            cursor = self.conn.execute(
                "UPDATE collection_run SET status = ?, finished_at = ? WHERE id = ?",
                (status, _iso(finished_at) if finished_at else None, run_id),
            )
        except Exception as exc:  # noqa: BLE001
            raise _wrap(exc) from exc
        if cursor.rowcount == 0:
            raise StorageError("collection run not found: %s" % run_id)

    @staticmethod
    def _from_row(row) -> CollectionRun:
        return CollectionRun(
            id=row["id"],
            week_key=row["week_key"],
            started_at=_parse_dt(row["started_at"]),
            finished_at=_parse_dt(row["finished_at"]),
            status=row["status"],
            config_snapshot=json.loads(row["config_snapshot"]),
            policy_version_id=row["policy_version_id"],
            created_at=_parse_dt(row["created_at"]),
        )


class RawSignalRepository:
    def __init__(self, conn):
        self.conn = conn

    def upsert(self, raw: RawSignal) -> RawSignal:
        """Insert ``raw`` or return the existing record for the same key."""
        try:
            self.conn.execute(
                "INSERT INTO raw_signal "
                "(id, collection_run_id, source, external_id, payload, "
                " payload_sha256, source_version, collected_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source, external_id, payload_sha256) DO NOTHING",
                (
                    raw.id,
                    raw.collection_run_id,
                    raw.source,
                    raw.external_id,
                    json.dumps(raw.payload, ensure_ascii=False),
                    raw.payload_sha256,
                    raw.source_version,
                    _iso(raw.collected_at),
                    _iso(raw.created_at),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            raise _wrap(exc) from exc
        row = self.conn.execute(
            "SELECT * FROM raw_signal WHERE source = ? AND external_id = ? AND payload_sha256 = ?",
            (raw.source, raw.external_id, raw.payload_sha256),
        ).fetchone()
        return self._from_row(row)

    def get(self, raw_id: str) -> Optional[RawSignal]:
        row = self.conn.execute(
            "SELECT * FROM raw_signal WHERE id = ?", (raw_id,)
        ).fetchone()
        return self._from_row(row) if row else None

    @staticmethod
    def _from_row(row) -> RawSignal:
        return RawSignal(
            id=row["id"],
            collection_run_id=row["collection_run_id"],
            source=row["source"],
            external_id=row["external_id"],
            payload=json.loads(row["payload"]),
            payload_sha256=row["payload_sha256"],
            source_version=row["source_version"],
            collected_at=_parse_dt(row["collected_at"]),
            created_at=_parse_dt(row["created_at"]),
        )


class SignalRepository:
    def __init__(self, conn):
        self.conn = conn

    def upsert(self, signal: Signal) -> Signal:
        """Insert ``signal`` or conditionally refresh the existing record.

        ``signal.last_seen_at`` carries the observation time (the raw signal
        ``collected_at``), never a wall-clock build time. On conflict:

        * ``first_seen_at``, ``created_at`` and ``state`` are preserved;
        * ``last_seen_at`` only moves forward;
        * ``raw_signal_id`` / ``collection_run_id`` / ``title`` / ``url`` /
          ``payload`` / ``updated_at`` are replaced only when the new
          observation is not older than the current one, so processing an old
          week or an old snapshot never overwrites a newer Signal.

        The ``state`` column is advanced separately via :meth:`advance_state`.
        """
        try:
            cur = self.conn.execute(
                "INSERT INTO signal "
                "(id, collection_run_id, source, canonical_key, raw_signal_id, "
                " title, url, signal_type, state, first_seen_at, last_seen_at, "
                " created_at, updated_at, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source, canonical_key) DO NOTHING",
                (
                    signal.id,
                    signal.collection_run_id,
                    signal.source,
                    signal.canonical_key,
                    signal.raw_signal_id,
                    signal.title,
                    signal.url,
                    signal.signal_type,
                    signal.state,
                    _iso(signal.first_seen_at),
                    _iso(signal.last_seen_at),
                    _iso(signal.created_at),
                    _iso(signal.updated_at),
                    json.dumps(signal.payload, ensure_ascii=False),
                ),
            )
            inserted = cur.rowcount == 1
        except Exception as exc:  # noqa: BLE001
            raise _wrap(exc) from exc

        existing = self._from_row(self._fetch_by_key(signal.source, signal.canonical_key))
        if inserted:
            return existing

        # Existing row: refresh monotonically.
        new_observed = signal.last_seen_at
        if new_observed is None or existing.last_seen_at is None:
            return existing
        if new_observed < existing.last_seen_at:
            return existing
        try:
            self.conn.execute(
                "UPDATE signal SET "
                " last_seen_at = ?, raw_signal_id = ?, collection_run_id = ?, "
                " title = ?, url = ?, payload = ?, updated_at = ? "
                "WHERE id = ?",
                (
                    _iso(new_observed),
                    signal.raw_signal_id,
                    signal.collection_run_id,
                    signal.title,
                    signal.url,
                    json.dumps(signal.payload, ensure_ascii=False),
                    _iso(signal.updated_at),
                    existing.id,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            raise _wrap(exc) from exc
        return self._from_row(self._fetch_by_key(signal.source, signal.canonical_key))

    def _fetch_by_key(self, source: str, canonical_key: str):
        try:
            return self.conn.execute(
                "SELECT * FROM signal WHERE source = ? AND canonical_key = ?",
                (source, canonical_key),
            ).fetchone()
        except Exception as exc:  # noqa: BLE001
            raise _wrap(exc) from exc

    def get(self, signal_id: str) -> Optional[Signal]:
        try:
            row = self.conn.execute(
                "SELECT * FROM signal WHERE id = ?", (signal_id,)
            ).fetchone()
        except Exception as exc:  # noqa: BLE001
            raise _wrap(exc) from exc
        return self._from_row(row) if row else None

    def get_state(self, signal_id: str) -> Optional[str]:
        """Return the current ``state`` of a signal, or ``None`` if absent."""
        try:
            row = self.conn.execute(
                "SELECT state FROM signal WHERE id = ?", (signal_id,)
            ).fetchone()
        except Exception as exc:  # noqa: BLE001
            raise _wrap(exc) from exc
        return row["state"] if row else None

    def advance_state(self, signal_id: str, from_state: str, to_state: str) -> bool:
        """Atomically advance ``state`` only if it currently equals ``from_state``.

        The guarded ``WHERE id = ? AND state = ?`` is a compare-and-swap: it
        returns ``True`` only when this call actually performed the transition,
        so concurrent or repeated runs cannot double-advance or regress a
        state. The accompanying ``state_transition`` audit append must happen
        in the same transaction as the caller's.
        """
        try:
            cur = self.conn.execute(
                "UPDATE signal SET state = ? WHERE id = ? AND state = ?",
                (to_state, signal_id, from_state),
            )
        except Exception as exc:  # noqa: BLE001
            raise _wrap(exc) from exc
        return cur.rowcount == 1

    @staticmethod
    def _from_row(row) -> Signal:
        return Signal(
            id=row["id"],
            collection_run_id=row["collection_run_id"],
            source=row["source"],
            canonical_key=row["canonical_key"],
            raw_signal_id=row["raw_signal_id"],
            title=row["title"],
            url=row["url"],
            signal_type=row["signal_type"],
            state=row["state"],
            first_seen_at=_parse_dt(row["first_seen_at"]),
            last_seen_at=_parse_dt(row["last_seen_at"]),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
            payload=json.loads(row["payload"]),
        )


class StateTransitionRepository:
    """Append-only audit log for state transitions."""

    def __init__(self, conn):
        self.conn = conn

    def append(self, record: StateTransitionRecord) -> None:
        transition_id = str(uuid.uuid4())
        try:
            self.conn.execute(
                "INSERT INTO state_transition "
                "(id, entity_id, from_state, to_state, timestamp, reason, stage, "
                " policy_version_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    transition_id,
                    record.entity_id,
                    record.from_state,
                    record.to_state,
                    _iso(record.timestamp),
                    record.reason,
                    record.stage,
                    record.policy_version_id,
                    _iso(now_utc()),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            raise _wrap(exc) from exc

    def list_for(self, entity_id: str) -> List[StateTransitionRecord]:
        rows = self.conn.execute(
            "SELECT * FROM state_transition WHERE entity_id = ? ORDER BY timestamp",
            (entity_id,),
        ).fetchall()
        return [
            StateTransitionRecord(
                entity_id=row["entity_id"],
                from_state=row["from_state"],
                to_state=row["to_state"],
                timestamp=_parse_dt(row["timestamp"]),
                reason=row["reason"],
                stage=row["stage"],
                policy_version_id=row["policy_version_id"],
            )
            for row in rows
        ]
