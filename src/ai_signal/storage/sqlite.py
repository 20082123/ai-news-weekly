"""SQLite engine and migration runner.

The connection is opened in autocommit mode (``isolation_level=None``) and
transactions are controlled explicitly with ``BEGIN`` / ``COMMIT`` /
``ROLLBACK``.

Why not ``executescript``?
``sqlite3.Connection.executescript`` issues an implicit ``COMMIT`` whenever
a transaction is pending. That would (a) undo an outer ``BEGIN`` and (b)
make a trailing ``COMMIT`` raise "no transaction is active", which breaks
the atomicity we need. We therefore split each migration into individual
statements and run them one by one with ``conn.execute`` inside a single
explicit transaction. If any statement fails the transaction is rolled
back, so a broken migration can never leave a stale ``schema_migration``
record behind.

No database file is created merely by importing this module.
"""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Mapping, Optional


class StorageError(Exception):
    """Base exception for storage problems."""


class MigrationError(StorageError):
    """Raised when a migration cannot be applied or its checksum changed."""


_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _open(path) -> sqlite3.Connection:
    conn = None
    try:
        conn = sqlite3.connect(str(path), isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn
    except (OSError, sqlite3.Error) as exc:
        if conn is not None:
            conn.close()
        raise StorageError("unable to open configured SQLite database: %s" % exc) from exc


@contextmanager
def connect(path):
    """Open a configured connection as a context manager."""
    conn = _open(path)
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:  # pragma: no cover - best effort close
            pass


# Bootstrapped in code so the runner can track migration 0001 itself. This
# definition is intentionally identical to the one inside 0001_initial.sql.
_SCHEMA_BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migration (
    version     INTEGER PRIMARY KEY,
    filename    TEXT NOT NULL UNIQUE,
    checksum    CHAR(64) NOT NULL,
    applied_at  TEXT NOT NULL
);
"""


def _split_sql_script(sql: str) -> List[str]:
    """Split a migration without breaking strings or trigger bodies."""
    statements = []
    buffer = ""
    for character in sql:
        buffer += character
        if character == ";" and sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                statements.append(statement)
            buffer = ""
    if buffer.strip():
        raise MigrationError("migration contains an incomplete SQL statement")
    return statements


def _run_in_transaction(conn, statements) -> None:
    """Execute ``statements`` inside one explicit BEGIN/COMMIT transaction.

    Any failure rolls the transaction back and is re-raised as a
    :class:`MigrationError` so callers (and the CLI) can map it to the
    database/migration exit code.
    """
    conn.execute("BEGIN")
    try:
        for statement in statements:
            conn.execute(statement)
        conn.execute("COMMIT")
    except Exception as exc:
        try:
            conn.execute("ROLLBACK")
        except Exception:  # pragma: no cover - rollback must never mask the error
            pass
        raise MigrationError("transaction failed and was rolled back: %s" % exc) from exc


def _schema_migration_exists(conn) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_migration'"
    ).fetchone()
    return row is not None


def _ensure_bootstrap(conn) -> None:
    _run_in_transaction(conn, _split_sql_script(_SCHEMA_BOOTSTRAP))


def _discover_migrations() -> List[tuple]:
    """Return ``[(version, path), ...]`` ordered by version."""
    discovered = []
    for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        version = int(path.stem.split("_", 1)[0])
        discovered.append((version, path))
    discovered.sort(key=lambda item: item[0])
    return discovered


def _applied_versions(conn) -> Mapping[int, str]:
    if not _schema_migration_exists(conn):
        return {}
    rows = conn.execute("SELECT version, checksum FROM schema_migration").fetchall()
    return {int(row["version"]): row["checksum"] for row in rows}


def _pending_migrations(conn) -> List[tuple]:
    applied = _applied_versions(conn)
    return [(version, path) for version, path in _discover_migrations() if version not in applied]


def _checksum(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise MigrationError("unable to read migration %s: %s" % (path.name, exc)) from exc


def _apply_migration(conn, version: int, path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    checksum = _checksum(path)
    statements = _split_sql_script(sql)
    # The schema_migration insert is parameterized and runs in the same
    # transaction as the DDL, so a DDL failure rolls back both.
    insert_sql = (
        "INSERT INTO schema_migration (version, filename, checksum, applied_at) "
        "VALUES (?, ?, ?, ?)"
    )
    conn.execute("BEGIN")
    try:
        for statement in statements:
            conn.execute(statement)
        conn.execute(
            insert_sql,
            (version, path.name, checksum, datetime.now(timezone.utc).isoformat()),
        )
        conn.execute("COMMIT")
    except Exception as exc:
        try:
            conn.execute("ROLLBACK")
        except Exception:  # pragma: no cover - rollback must never mask the error
            pass
        raise MigrationError("failed to apply migration %s: %s" % (path.name, exc)) from exc


def apply_migrations(conn) -> Mapping[str, Any]:
    """Apply all pending migrations, idempotently.

    * already-applied migrations with a matching checksum are skipped;
    * an already-applied migration whose checksum changed raises
      :class:`MigrationError` (the file was edited after being applied);
    * each new migration is applied atomically.
    """
    _ensure_bootstrap(conn)
    applied = _applied_versions(conn)
    for version, path in _discover_migrations():
        checksum = _checksum(path)
        if version in applied:
            if applied[version] != checksum:
                raise MigrationError(
                    "checksum mismatch for migration %s: stored %s != file %s"
                    % (path.name, applied[version], checksum)
                )
            continue
        _apply_migration(conn, version, path)
    return schema_status(conn)


def schema_status(conn) -> Mapping[str, Any]:
    """Return a description of the current schema state."""
    if not _schema_migration_exists(conn):
        return {
            "initialized": False,
            "applied": [],
            "latest_applied": None,
            "target": None,
            "checksum_valid": True,
            "up_to_date": False,
        }
    rows = conn.execute(
        "SELECT version, filename, checksum, applied_at FROM schema_migration ORDER BY version"
    ).fetchall()
    applied = [
        {
            "version": int(row["version"]),
            "filename": row["filename"],
            "checksum": row["checksum"],
            "applied_at": row["applied_at"],
        }
        for row in rows
    ]
    discovered = _discover_migrations()
    target = discovered[-1][0] if discovered else None
    latest = applied[-1]["version"] if applied else None
    applied_by_version = {entry["version"]: entry for entry in applied}
    applied_set = set(applied_by_version)
    checksum_valid = True
    for version, path in discovered:
        entry = applied_by_version.get(version)
        if entry is not None and entry["checksum"] != _checksum(path):
            checksum_valid = False
    up_to_date = (
        target is not None
        and latest == target
        and all(version in applied_set for version, _ in discovered)
        and checksum_valid
    )
    return {
        "initialized": True,
        "applied": applied,
        "latest_applied": latest,
        "target": target,
        "checksum_valid": checksum_valid,
        "up_to_date": up_to_date,
    }


def initialize_database(path) -> Mapping[str, Any]:
    """Create or migrate the database at ``path``.

    A brand new database is created without any backup. When an existing
    database already has our schema and still has pending migrations to
    apply, a timestamped backup is written next to it first. This backup
    path is not exercised in phase 1 (only migration 0001 exists) but the
    logic is in place for future versions.
    """
    target = Path(path)
    _backup_before_upgrade(target)
    conn = _open(target)
    try:
        return apply_migrations(conn)
    finally:
        conn.close()


def _backup_before_upgrade(target: Path) -> Optional[Path]:
    """Back up any non-empty existing database before schema writes.

    A plain inspection connection is used deliberately: it does not switch
    journal mode or create ``schema_migration`` before the consistent SQLite
    backup is complete. This also protects databases not yet managed by this
    migration runner.
    """
    try:
        if not target.is_file() or target.stat().st_size == 0:
            return None
    except OSError as exc:
        raise StorageError("unable to inspect existing database: %s" % exc) from exc
    inspection = None
    backup_conn = None
    try:
        inspection = sqlite3.connect(str(target), isolation_level=None)
        inspection.row_factory = sqlite3.Row
        pending = _pending_migrations(inspection)
        if not pending:
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        backup = target.with_name("%s.bak.%s%s" % (target.stem, stamp, target.suffix))
        backup_conn = sqlite3.connect(str(backup))
        inspection.backup(backup_conn)
        return backup
    except (OSError, sqlite3.Error) as exc:
        raise StorageError("unable to back up database before migration: %s" % exc) from exc
    finally:
        if backup_conn is not None:
            backup_conn.close()
        if inspection is not None:
            inspection.close()
