"""Repositories for the phase 2A source-collection tables.

* :class:`SourceRunRepository` - insert / get / list per collection run
* :class:`SourceCursorRepository` - read and advance the per-scope cursor
* :class:`RawSignalObservationRepository` - per-run observation attribution

Cursor progress is isolated by ``(source, scope_key)``: each scope keeps its
own row, so different GitHub query scopes advance independently.

Rules (mirroring :mod:`ai_signal.storage.repositories`):

* all SQL is parameterized;
* repositories never call ``COMMIT`` - the caller owns the transaction, so a
  caller-initiated rollback cannot leave half-written rows behind;
* every database error is surfaced as :class:`StorageError`;
* ``warnings`` are stored as a JSON array and restored as a tuple of strings;
* ``cursor_advanced`` is stored as 0/1 and restored as a bool.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import List, Optional

from ..domain.models import SourceCursor, SourceRun, ensure_aware_utc, now_utc
from .sqlite import StorageError


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    return ensure_aware_utc(value).isoformat()


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _wrap(operation: str) -> StorageError:
    return StorageError("source storage %s failed" % operation)


class SourceRunRepository:
    def __init__(self, conn):
        self.conn = conn

    def insert(self, run: SourceRun) -> SourceRun:
        try:
            self.conn.execute(
                "INSERT INTO source_run "
                "(id, collection_run_id, source, scope_key, source_version, status, "
                " started_at, finished_at, cursor_in, cursor_out, item_count, "
                " warning_count, warnings, cursor_advanced, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run.id,
                    run.collection_run_id,
                    run.source,
                    run.scope_key,
                    run.source_version,
                    run.status,
                    _iso(run.started_at),
                    _iso(run.finished_at),
                    run.cursor_in,
                    run.cursor_out,
                    run.item_count,
                    run.warning_count,
                    json.dumps(list(run.warnings), ensure_ascii=False),
                    1 if run.cursor_advanced else 0,
                    _iso(run.created_at),
                ),
            )
        except Exception as exc:  # noqa: BLE001 - surface as StorageError
            raise _wrap("insert") from exc
        return run

    def get(self, run_id: str) -> Optional[SourceRun]:
        try:
            row = self.conn.execute(
                "SELECT * FROM source_run WHERE id = ?", (run_id,)
            ).fetchone()
            return self._from_row(row) if row else None
        except StorageError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface as StorageError
            raise _wrap("read") from exc

    def list_for_collection_run(self, collection_run_id: str) -> List[SourceRun]:
        try:
            rows = self.conn.execute(
                "SELECT * FROM source_run WHERE collection_run_id = ? "
                "ORDER BY source, scope_key",
                (collection_run_id,),
            ).fetchall()
            return [self._from_row(row) for row in rows]
        except StorageError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface as StorageError
            raise _wrap("list") from exc

    @staticmethod
    def _from_row(row) -> SourceRun:
        warnings = json.loads(row["warnings"])
        if not isinstance(warnings, list) or not all(
            isinstance(warning, str) for warning in warnings
        ):
            raise ValueError("stored warnings must be a JSON string array")
        if row["cursor_advanced"] not in (0, 1):
            raise ValueError("stored cursor_advanced must be 0 or 1")
        return SourceRun(
            id=row["id"],
            collection_run_id=row["collection_run_id"],
            source=row["source"],
            scope_key=row["scope_key"],
            source_version=row["source_version"],
            status=row["status"],
            started_at=_parse_dt(row["started_at"]),
            finished_at=_parse_dt(row["finished_at"]),
            cursor_in=row["cursor_in"],
            cursor_out=row["cursor_out"],
            item_count=int(row["item_count"]),
            warning_count=int(row["warning_count"]),
            warnings=tuple(str(w) for w in warnings),
            cursor_advanced=bool(row["cursor_advanced"]),
            created_at=_parse_dt(row["created_at"]),
        )


class SourceCursorRepository:
    def __init__(self, conn):
        self.conn = conn

    def get(self, source: str, scope_key: str) -> Optional[SourceCursor]:
        try:
            row = self.conn.execute(
                "SELECT * FROM source_cursor WHERE source = ? AND scope_key = ?",
                (source, scope_key),
            ).fetchone()
            return self._from_row(row) if row else None
        except StorageError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface as StorageError
            raise _wrap("cursor read") from exc

    def advance(self, cursor: SourceCursor) -> SourceCursor:
        """Upsert the cursor for ``(cursor.source, cursor.scope_key)``.

        Only the pipeline decides when to call this (on a successful batch
        with a distinct, non-null next cursor), so advancing never happens
        implicitly. Different scope keys coexist independently.
        """
        try:
            self.conn.execute(
                "INSERT INTO source_cursor "
                "(source, scope_key, cursor, source_version, last_run_id, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source, scope_key) DO UPDATE SET "
                " cursor = excluded.cursor, "
                " source_version = excluded.source_version, "
                " last_run_id = excluded.last_run_id, "
                " updated_at = excluded.updated_at",
                (
                    cursor.source,
                    cursor.scope_key,
                    cursor.cursor,
                    cursor.source_version,
                    cursor.last_run_id,
                    _iso(cursor.updated_at),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            raise _wrap("cursor advance") from exc
        stored = self.get(cursor.source, cursor.scope_key)
        if stored is None:  # pragma: no cover - defensive against driver anomalies
            raise _wrap("cursor advance")
        return stored

    @staticmethod
    def _from_row(row) -> SourceCursor:
        return SourceCursor(
            source=row["source"],
            scope_key=row["scope_key"],
            source_version=row["source_version"],
            last_run_id=row["last_run_id"],
            updated_at=_parse_dt(row["updated_at"]),
            cursor=row["cursor"],
        )


class RawSignalObservationRepository:
    """Records that a specific ``source_run`` observed a ``raw_signal``.

    ``raw_signal`` is content-addressed and globally deduplicated, so its own
    ``collection_run_id`` only records the first run that saw that content. A
    snapshot collected again by another scope or another week is recorded here
    (one row per ``(source_run_id, raw_signal_id)``), preserving the
    observation attribution without duplicating the snapshot.
    """

    def __init__(self, conn):
        self.conn = conn

    def insert(self, source_run_id: str, raw_signal_id: str, observed_at) -> None:
        """Record one observation. Idempotent per ``(source_run_id, raw_signal_id)``.

        Any database failure propagates as :class:`StorageError` so the caller's
        whole batch transaction rolls back; this repository never commits.
        """
        try:
            self.conn.execute(
                "INSERT INTO raw_signal_observation "
                "(source_run_id, raw_signal_id, observed_at, created_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(source_run_id, raw_signal_id) DO NOTHING",
                (source_run_id, raw_signal_id, _iso(observed_at), _iso(now_utc())),
            )
        except Exception as exc:  # noqa: BLE001 - surface as StorageError
            raise _wrap("observation insert") from exc
