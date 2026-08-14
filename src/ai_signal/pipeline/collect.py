"""Single-source collection orchestration (phase 2A).

:func:`collect_source_once` runs one offline fixture-backed collection for a
source and records the result atomically. The data flow is::

    CollectionRun(running) [own transaction]
        -> read SourceCursor
        -> source.collect(...)              (outside any db transaction)
        -> single transaction:
               SourceRun
               SourceItem -> RawSignal (deterministic canonical-hash upsert)
               advance SourceCursor  (only on success)
               CollectionRun -> final status

Design rules enforced here:

* ``config_snapshot`` may only carry safe state (shadow mode, github source,
  fixture flag, fixture SHA-256) - never a path, an env var or a credential;
* the source is collected *outside* any database transaction;
* the cursor advance and every raw-signal write share one final transaction,
  so a failure rolls both back and never leaves half-written state;
* a cursor is advanced only after a successful batch supplies a distinct,
  non-null next cursor;
* if the final transaction fails, it is rolled back and the collection run is
  best-effort marked ``failed`` *without masking the original* ``StorageError``;
* nothing logged or returned contains a payload, a URL, a fixture path or an
  exception message.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from ..domain.models import (
    CollectionRun,
    RawSignal,
    SourceCursor,
    SourceRun,
    now_utc,
    sha256_hex,
    validate_scope_key,
)
from ..observability.logging import StructuredLogger
from ..sources.github import GitHubClient, GitHubSource
from ..storage import sqlite as sqlite_storage
from ..storage.repositories import CollectionRunRepository, RawSignalRepository
from ..storage.source_repositories import (
    RawSignalObservationRepository,
    SourceCursorRepository,
    SourceRunRepository,
)


class CollectionPolicyError(Exception):
    """Raised when the requested collection violates the security policy."""


_ADAPTER_KIND_FIXTURE = "fixture"
_ADAPTER_KIND_REST = "github-rest-v1"

_FIXTURE_CONFIG_KEYS = frozenset(
    {"run_mode", "source", "scope_key", "adapter_kind", "fixture", "fixture_sha256"}
)
_REST_CONFIG_KEYS = frozenset(
    {
        "run_mode",
        "source",
        "scope_key",
        "adapter_kind",
        "query_sha256",
        "sort",
        "order",
        "per_page",
        "max_pages",
    }
)


def _require_hex64(label: str, value: Any) -> None:
    if not isinstance(value, str) or len(value) != 64:
        raise CollectionPolicyError("%s must be a 64-character hex string" % label)
    try:
        int(value, 16)
    except ValueError as exc:
        raise CollectionPolicyError("%s must be a hex string" % label) from exc


def _validate_fixture_fields(config_snapshot: Mapping[str, Any]) -> None:
    if config_snapshot.get("fixture") is not True:
        raise CollectionPolicyError("only fixture-backed collection is permitted")
    _require_hex64("fixture_sha256", config_snapshot.get("fixture_sha256"))


def _validate_rest_fields(config_snapshot: Mapping[str, Any]) -> None:
    _require_hex64("query_sha256", config_snapshot.get("query_sha256"))
    if config_snapshot.get("sort") not in ("updated", "stars"):
        raise CollectionPolicyError("sort must be 'updated' or 'stars'")
    if config_snapshot.get("order") not in ("asc", "desc"):
        raise CollectionPolicyError("order must be 'asc' or 'desc'")
    per_page = config_snapshot.get("per_page")
    if isinstance(per_page, bool) or not isinstance(per_page, int) or not 1 <= per_page <= 25:
        raise CollectionPolicyError("per_page must be between 1 and 25")
    max_pages = config_snapshot.get("max_pages")
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or not 1 <= max_pages <= 3:
        raise CollectionPolicyError("max_pages must be between 1 and 3")


@dataclass(frozen=True)
class CollectionResult:
    """Safe, payload-free summary of one collection run."""

    run_id: str
    source: str
    status: str
    processed_item_count: int
    warning_count: int
    cursor_advanced: bool


def _validate_collection_config(
    source: str, scope_key: str, config_snapshot: Any
) -> None:
    if not isinstance(config_snapshot, Mapping):
        raise CollectionPolicyError("config_snapshot must be a mapping")
    adapter_kind = config_snapshot.get("adapter_kind")
    if adapter_kind == _ADAPTER_KIND_FIXTURE:
        allowed = _FIXTURE_CONFIG_KEYS
    elif adapter_kind == _ADAPTER_KIND_REST:
        allowed = _REST_CONFIG_KEYS
    else:
        raise CollectionPolicyError("unsupported adapter_kind")
    extra = set(config_snapshot.keys()) - allowed
    if extra:
        raise CollectionPolicyError("config_snapshot contains disallowed keys")
    # Shared invariants for both adapters.
    if config_snapshot.get("run_mode") != "shadow":
        raise CollectionPolicyError("only shadow run_mode is permitted")
    if config_snapshot.get("source") != source:
        raise CollectionPolicyError("config_snapshot source mismatch")
    if config_snapshot.get("scope_key") != scope_key:
        raise CollectionPolicyError("config_snapshot scope_key mismatch")
    if adapter_kind == _ADAPTER_KIND_FIXTURE:
        _validate_fixture_fields(config_snapshot)
    else:
        _validate_rest_fields(config_snapshot)


def _canonical_payload_hash(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return sha256_hex(canonical)


def _batch_to_run_status(batch_status: str) -> str:
    if batch_status == "success":
        return "success"
    if batch_status in ("partial", "unavailable"):
        return "partial"
    return "failed"


def _best_effort_mark_failed(db_path: Any, run_id: str, finished_at: Any) -> None:
    """Try to mark a run failed after a transaction failure.

    Swallows every error: it must never mask the original ``StorageError``
    raised by the caller.
    """
    try:
        conn = sqlite_storage._open(db_path)
        try:
            conn.execute("BEGIN")
            CollectionRunRepository(conn).update_status(
                run_id, "failed", finished_at=finished_at
            )
            conn.execute("COMMIT")
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - best effort, never mask the original error
        pass


def _rollback_quietly(conn) -> None:
    try:
        conn.execute("ROLLBACK")
    except Exception:  # pragma: no cover - rollback must never mask the error
        pass


def _safe_log(
    logger: Optional[StructuredLogger], level: str, event: str, **fields: Any
) -> None:
    """Emit only caller-supplied safe fields and never affect collection."""
    if logger is None:
        return
    try:
        getattr(logger, level)(event, **fields)
    except Exception:  # pragma: no cover - observability must not break the run
        pass


def collect_source_once(
    db_path: Any,
    source: str,
    week_key: str,
    *,
    scope_key: str,
    client: GitHubClient,
    config_snapshot: Mapping[str, Any],
    logger: Optional[StructuredLogger] = None,
) -> CollectionResult:
    """Run one collection for ``source`` (one page per call).

    ``scope_key`` isolates cursor progress between different logical query
    scopes (for example ``ai-agents-v1``). Two adapter kinds are accepted via
    ``config_snapshot["adapter_kind"]``: ``"fixture"`` (offline JSON fixture)
    and ``"github-rest-v1"`` (read-only public GitHub Search API). The raw
    GitHub query never enters the config snapshot; only its SHA-256 does.
    """
    if source != "github":
        raise CollectionPolicyError("unsupported source: %s" % source)
    try:
        validate_scope_key(scope_key)
    except (TypeError, ValueError) as exc:
        raise CollectionPolicyError("invalid scope_key") from exc
    _validate_collection_config(source, scope_key, config_snapshot)

    # Ensure schema (0001 + 0002) is applied. Raises StorageError on failure.
    sqlite_storage.initialize_database(db_path)

    started_at = now_utc()
    run = CollectionRun(
        week_key=week_key,
        started_at=started_at,
        config_snapshot=config_snapshot,
        status="running",
    )

    # Rule 3: persist the CollectionRun(running) in its own transaction, then
    # read the current cursor for this (source, scope_key) on the same
    # connection.
    conn = sqlite_storage._open(db_path)
    try:
        try:
            conn.execute("BEGIN")
            CollectionRunRepository(conn).insert(run)
            conn.execute("COMMIT")
            cursor_state = SourceCursorRepository(conn).get(source, scope_key)
            cursor_in = cursor_state.cursor if cursor_state is not None else None
        except sqlite_storage.StorageError:
            _rollback_quietly(conn)
            raise
        except Exception as exc:  # noqa: BLE001 - present a stable storage error
            _rollback_quietly(conn)
            raise sqlite_storage.StorageError(
                "failed to persist collection run"
            ) from exc
    finally:
        conn.close()

    _safe_log(
        logger,
        "info",
        "source_collection_started",
        run_id=run.id,
        source=source,
        status="running",
    )

    # Rule 5: collect outside any database transaction. The scope key is
    # carried in the context for future sources but is never logged here.
    source_obj = GitHubSource(client)
    context = {
        "run_id": run.id,
        "week_key": week_key,
        "source": source,
        "scope_key": scope_key,
    }
    batch = source_obj.collect(cursor_in, context)

    advance_cursor = (
        batch.status == "success"
        and batch.next_cursor is not None
        and batch.next_cursor != cursor_in
    )
    run_status = _batch_to_run_status(batch.status)

    # Rule 6 + 7: one final transaction for source_run, raw signals and the
    # cursor advance (plus the collection-run status update).
    conn = sqlite_storage._open(db_path)
    pending_exc: Optional[Exception] = None
    try:
        conn.execute("BEGIN")
        source_run = SourceRunRepository(conn).insert(
            SourceRun(
                collection_run_id=run.id,
                source=source,
                scope_key=scope_key,
                source_version=batch.source_version,
                status=batch.status,
                started_at=batch.started_at,
                finished_at=batch.finished_at,
                cursor_in=cursor_in,
                cursor_out=batch.next_cursor,
                item_count=len(batch.items),
                warning_count=len(batch.warnings),
                warnings=batch.warnings,
                cursor_advanced=advance_cursor,
                created_at=now_utc(),
            )
        )
        raw_repo = RawSignalRepository(conn)
        obs_repo = RawSignalObservationRepository(conn)
        for item in batch.items:
            # ``stored`` is the canonical raw_signal row, which may be a
            # globally-deduplicated existing row for a snapshot seen before.
            stored = raw_repo.upsert(
                RawSignal(
                    collection_run_id=run.id,
                    source=source,
                    external_id=item.external_id,
                    payload=item.payload,
                    payload_sha256=_canonical_payload_hash(item.payload),
                    collected_at=item.collected_at,
                    source_version=batch.source_version,
                )
            )
            # Attribute this observation of the (possibly shared) snapshot to
            # the current source_run, so materialization can select it per
            # (week_key, scope_key) even when the raw content was deduplicated.
            obs_repo.insert(source_run.id, stored.id, item.collected_at)
        if advance_cursor:
            SourceCursorRepository(conn).advance(
                SourceCursor(
                    source=source,
                    scope_key=scope_key,
                    source_version=batch.source_version,
                    last_run_id=run.id,
                    updated_at=now_utc(),
                    cursor=batch.next_cursor,
                )
            )
        CollectionRunRepository(conn).update_status(
            run.id, run_status, finished_at=batch.finished_at
        )
        conn.execute("COMMIT")
    except Exception as exc:  # noqa: BLE001 - capture, rollback, re-raise safely
        pending_exc = (
            exc
            if isinstance(exc, sqlite_storage.StorageError)
            else sqlite_storage.StorageError("failed to persist collected source batch")
        )
        _rollback_quietly(conn)
    finally:
        conn.close()

    if pending_exc is not None:
        # Rule 8: best-effort mark failed, then propagate the original error.
        _best_effort_mark_failed(db_path, run.id, now_utc())
        _safe_log(
            logger,
            "error",
            "source_collection_persist_failed",
            run_id=run.id,
            source=source,
            status="failed",
            error_code="STORAGE_ERROR",
        )
        raise pending_exc

    _safe_log(
        logger,
        "info",
        "source_collection_completed",
        run_id=run.id,
        source=source,
        status=batch.status,
        processed_item_count=len(batch.items),
        warning_count=len(batch.warnings),
        cursor_advanced=advance_cursor,
    )

    return CollectionResult(
        run_id=run.id,
        source=source,
        status=batch.status,
        processed_item_count=len(batch.items),
        warning_count=len(batch.warnings),
        cursor_advanced=advance_cursor,
    )
