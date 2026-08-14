"""Integration tests for :mod:`ai_signal.storage.source_repositories`."""

import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.domain.models import CollectionRun, SourceCursor, SourceRun  # noqa: E402
from ai_signal.storage import sqlite as S  # noqa: E402
from ai_signal.storage.repositories import CollectionRunRepository  # noqa: E402
from ai_signal.storage.source_repositories import (  # noqa: E402
    SourceCursorRepository,
    SourceRunRepository,
)

AWARE = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)
SCOPE = "github-fixture-v1"


class SourceRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai_signal_2a_repo_")
        self.db = os.path.join(self.tmp, "test.db")
        S.initialize_database(self.db)
        self.conn = S._open(self.db)
        self.run = CollectionRun(week_key="2026-W33", started_at=AWARE, config_snapshot={})
        CollectionRunRepository(self.conn).insert(self.run)

    def tearDown(self):
        try:
            self.conn.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _source_run(self, **overrides):
        defaults = dict(
            collection_run_id=self.run.id,
            source="github",
            scope_key=SCOPE,
            source_version="fixture-1",
            status="success",
            started_at=AWARE,
            finished_at=AWARE,
            cursor_out="c1",
            item_count=1,
            warning_count=1,
            warnings=("w1",),
            cursor_advanced=True,
        )
        defaults.update(overrides)
        return SourceRun(**defaults)

    def _cursor(self, **overrides):
        defaults = dict(
            source="github",
            scope_key=SCOPE,
            source_version="fixture-1",
            last_run_id=self.run.id,
            updated_at=AWARE,
            cursor="c1",
        )
        defaults.update(overrides)
        return SourceCursor(**defaults)

    def test_source_run_insert_get_list_round_trip_scope_key(self):
        repo = SourceRunRepository(self.conn)
        source_run = repo.insert(self._source_run())
        fetched = repo.get(source_run.id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.source, "github")
        self.assertEqual(fetched.scope_key, SCOPE)
        self.assertEqual(fetched.item_count, 1)
        self.assertTrue(fetched.cursor_advanced)
        listed = repo.list_for_collection_run(self.run.id)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].scope_key, SCOPE)

    def test_warning_tuple_round_trip(self):
        repo = SourceRunRepository(self.conn)
        source_run = repo.insert(
            self._source_run(warnings=("a", "b", "c"), warning_count=3)
        )
        fetched = repo.get(source_run.id)
        self.assertEqual(fetched.warnings, ("a", "b", "c"))
        self.assertIsInstance(fetched.warnings, tuple)

    def test_source_run_rejects_inconsistent_audit_fields(self):
        with self.assertRaises(ValueError):
            self._source_run(warning_count=0)
        with self.assertRaises(ValueError):
            self._source_run(cursor_in="c1", cursor_out="c1", cursor_advanced=True)

    def test_corrupt_warning_json_is_wrapped(self):
        repo = SourceRunRepository(self.conn)
        source_run = repo.insert(self._source_run())
        self.conn.execute(
            "UPDATE source_run SET warnings = ? WHERE id = ?", ("{bad", source_run.id)
        )
        with self.assertRaises(S.StorageError):
            repo.get(source_run.id)

    def test_read_on_closed_connection_is_wrapped(self):
        repo = SourceCursorRepository(self.conn)
        self.conn.close()
        with self.assertRaises(S.StorageError):
            repo.get("github", SCOPE)

    def test_source_cursor_first_write_and_update(self):
        repo = SourceCursorRepository(self.conn)
        self.assertIsNone(repo.get("github", SCOPE))
        repo.advance(self._cursor(cursor="c1"))
        first = repo.get("github", SCOPE)
        self.assertEqual(first.cursor, "c1")
        self.assertEqual(first.scope_key, SCOPE)
        repo.advance(self._cursor(cursor="c2"))
        second = repo.get("github", SCOPE)
        self.assertEqual(second.cursor, "c2")
        count = self.conn.execute("SELECT COUNT(*) FROM source_cursor").fetchone()[0]
        self.assertEqual(count, 1)

    def test_source_run_foreign_key_failure(self):
        repo = SourceRunRepository(self.conn)
        with self.assertRaises(S.StorageError):
            repo.insert(self._source_run(collection_run_id="nonexistent-run"))

    def test_source_cursor_foreign_key_failure(self):
        repo = SourceCursorRepository(self.conn)
        with self.assertRaises(S.StorageError):
            repo.advance(self._cursor(last_run_id="nonexistent-run"))

    def test_database_rejects_null_cursor(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO source_cursor "
                "(source, scope_key, cursor, source_version, last_run_id, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("github", SCOPE, None, "fixture-1", self.run.id, AWARE.isoformat()),
            )

    def test_database_rejects_false_cursor_advance(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO source_run "
                "(id, collection_run_id, source, scope_key, source_version, status, "
                "started_at, finished_at, cursor_in, cursor_out, item_count, "
                "warning_count, warnings, cursor_advanced, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "invalid-source-run",
                    self.run.id,
                    "github",
                    SCOPE,
                    "fixture-1",
                    "success",
                    AWARE.isoformat(),
                    AWARE.isoformat(),
                    "c1",
                    "c1",
                    0,
                    0,
                    "[]",
                    1,
                    AWARE.isoformat(),
                ),
            )

    def test_rollback_leaves_no_source_run(self):
        repo = SourceRunRepository(self.conn)
        self.conn.execute("BEGIN")
        repo.insert(self._source_run())
        self.conn.execute("ROLLBACK")
        count = self.conn.execute("SELECT COUNT(*) FROM source_run").fetchone()[0]
        self.assertEqual(count, 0)

    # ----- scope_key isolation regressions ----- #

    def test_two_cursor_scopes_coexist_independently(self):
        repo = SourceCursorRepository(self.conn)
        repo.advance(self._cursor(scope_key="scope-a-v1", cursor="c1"))
        repo.advance(self._cursor(scope_key="scope-b-v1", cursor="c2"))
        self.assertEqual(repo.get("github", "scope-a-v1").cursor, "c1")
        self.assertEqual(repo.get("github", "scope-b-v1").cursor, "c2")
        count = self.conn.execute("SELECT COUNT(*) FROM source_cursor").fetchone()[0]
        self.assertEqual(count, 2)
        # Advancing one scope must not touch the other.
        repo.advance(self._cursor(scope_key="scope-a-v1", cursor="c3"))
        self.assertEqual(repo.get("github", "scope-a-v1").cursor, "c3")
        self.assertEqual(repo.get("github", "scope-b-v1").cursor, "c2")

    def test_source_run_coexists_across_scopes(self):
        repo = SourceRunRepository(self.conn)
        repo.insert(self._source_run(scope_key="scope-a-v1"))
        repo.insert(self._source_run(scope_key="scope-b-v1"))
        listed = repo.list_for_collection_run(self.run.id)
        self.assertEqual(len(listed), 2)
        self.assertEqual({row.scope_key for row in listed}, {"scope-a-v1", "scope-b-v1"})

    def test_source_run_duplicate_scope_is_rejected(self):
        repo = SourceRunRepository(self.conn)
        repo.insert(self._source_run(scope_key="scope-a-v1"))
        with self.assertRaises(S.StorageError):
            repo.insert(self._source_run(scope_key="scope-a-v1"))

    def test_database_rejects_empty_scope_key_cursor(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO source_cursor "
                "(source, scope_key, cursor, source_version, last_run_id, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("github", "", "c1", "fixture-1", self.run.id, AWARE.isoformat()),
            )


class ScopeKeyValidationTest(unittest.TestCase):
    """The model layer must reject any scope_key that is not a stable alias."""

    _BAD_SCOPE_KEYS = (
        "",
        "UpperCase",
        "has space",
        "slash/here",
        "a" * 65,            # over 64 characters
        "ai-agents:foo>bar",  # raw-query style with colon / greater-than
        "valid-looking-v1\n",  # '$' alone would accept a final newline
        ".leading-dot",
        "-leading-dash",
    )

    def test_source_run_rejects_invalid_scope_key(self):
        for bad in self._BAD_SCOPE_KEYS:
            with self.subTest(scope_key=bad):
                with self.assertRaises((ValueError, TypeError)):
                    SourceRun(
                        collection_run_id="run-1",
                        source="github",
                        scope_key=bad,
                        source_version="fixture-1",
                        status="success",
                        started_at=AWARE,
                        finished_at=AWARE,
                        cursor_out="c1",
                        item_count=1,
                        warning_count=1,
                        warnings=("w1",),
                        cursor_advanced=True,
                    )

    def test_source_cursor_rejects_invalid_scope_key(self):
        for bad in self._BAD_SCOPE_KEYS:
            with self.subTest(scope_key=bad):
                with self.assertRaises((ValueError, TypeError)):
                    SourceCursor(
                        source="github",
                        scope_key=bad,
                        source_version="fixture-1",
                        last_run_id="run-1",
                        updated_at=AWARE,
                        cursor="c1",
                    )

    def test_valid_scope_keys_are_accepted(self):
        for good in ("a", "github-fixture-v1", "ai-agents-v2", "new.products_v1"):
            with self.subTest(scope_key=good):
                SourceCursor(
                    source="github",
                    scope_key=good,
                    source_version="fixture-1",
                    last_run_id="run-1",
                    updated_at=AWARE,
                    cursor="c1",
                )


if __name__ == "__main__":
    unittest.main()
