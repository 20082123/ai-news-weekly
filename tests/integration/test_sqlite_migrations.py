"""Integration tests for the SQLite engine and migration runner."""

import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.storage import sqlite as S  # noqa: E402

_EXPECTED_TABLES = (
    "schema_migration",
    "policy_version",
    "collection_run",
    "raw_signal",
    "signal",
    "state_transition",
    "event",
    "event_member",
    "claim",
    "evidence",
    "claim_evidence",
    "material_pack",
    "feedback",
    "publication",
    "metric_snapshot",
    "score_log",
    "delivery_run",
)


class MigrationsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai_signal_test_")
        self.db = os.path.join(self.tmp, "test.db")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fresh_initialization(self):
        status = S.initialize_database(self.db)
        self.assertTrue(status["initialized"])
        self.assertTrue(status["up_to_date"])
        self.assertEqual(status["latest_applied"], 1)
        self.assertEqual(status["target"], 1)

    def test_repeated_initialization_is_idempotent(self):
        first = S.initialize_database(self.db)
        self.assertTrue(first["up_to_date"])
        second = S.initialize_database(self.db)
        self.assertTrue(second["up_to_date"])
        with S.connect(self.db) as conn:
            count = conn.execute("SELECT COUNT(*) FROM schema_migration").fetchone()[0]
        self.assertEqual(count, 1)

    def test_schema_status_on_initialized_db(self):
        S.initialize_database(self.db)
        with S.connect(self.db) as conn:
            status = S.schema_status(conn)
        self.assertTrue(status["initialized"])
        self.assertEqual(len(status["applied"]), 1)
        self.assertEqual(status["applied"][0]["filename"], "0001_initial.sql")

    def test_all_tables_created(self):
        S.initialize_database(self.db)
        with S.connect(self.db) as conn:
            names = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        for table in _EXPECTED_TABLES:
            self.assertIn(table, names)

    def test_connection_pragmas(self):
        S.initialize_database(self.db)
        with S.connect(self.db) as conn:
            foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        self.assertEqual(foreign_keys, 1)
        self.assertEqual(journal_mode.lower(), "wal")
        self.assertEqual(busy_timeout, 5000)

    def test_checksum_mismatch_raises(self):
        S.initialize_database(self.db)
        with S.connect(self.db) as conn:
            conn.execute("UPDATE schema_migration SET checksum = 'deadbeef' WHERE version = 1")
        with self.assertRaises(S.MigrationError):
            with S.connect(self.db) as conn:
                S.apply_migrations(conn)

    def test_schema_status_reports_checksum_mismatch(self):
        S.initialize_database(self.db)
        with S.connect(self.db) as conn:
            conn.execute("UPDATE schema_migration SET checksum = 'deadbeef' WHERE version = 1")
            status = S.schema_status(conn)
        self.assertFalse(status["checksum_valid"])
        self.assertFalse(status["up_to_date"])

    def test_sql_splitter_preserves_semicolons_in_strings(self):
        statements = S._split_sql_script(
            "CREATE TABLE one (value TEXT DEFAULT ';'); CREATE TABLE two (id INTEGER);"
        )
        self.assertEqual(len(statements), 2)

    def test_invalid_parent_path_is_wrapped(self):
        missing = os.path.join(self.tmp, "missing", "test.db")
        with self.assertRaises(S.StorageError):
            S.initialize_database(missing)

    def test_migration_failure_rolls_back(self):
        # Simulate a future broken migration in an isolated migrations dir.
        fake_dir = os.path.join(self.tmp, "migrations")
        os.makedirs(fake_dir)
        real_dir = pathlib.Path(S._MIGRATIONS_DIR)
        shutil.copy2(real_dir / "0001_initial.sql", fake_dir)
        bad = pathlib.Path(fake_dir) / "0002_broken.sql"
        bad.write_text(
            "CREATE TABLE broken (id INTEGER PRIMARY KEY); THIS IS NOT VALID SQL;",
            encoding="utf-8",
        )
        original = S._MIGRATIONS_DIR
        S._MIGRATIONS_DIR = pathlib.Path(fake_dir)
        try:
            with self.assertRaises(Exception):
                S.initialize_database(self.db)
        finally:
            S._MIGRATIONS_DIR = original

        with S.connect(self.db) as conn:
            versions = [
                row[0]
                for row in conn.execute(
                    "SELECT version FROM schema_migration ORDER BY version"
                )
            ]
            names = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        self.assertEqual(versions, [1])
        self.assertNotIn("broken", names)

    def test_existing_database_is_backed_up_before_upgrade(self):
        fake_dir = pathlib.Path(self.tmp) / "upgrade_migrations"
        fake_dir.mkdir()
        real_dir = pathlib.Path(S._MIGRATIONS_DIR)
        shutil.copy2(real_dir / "0001_initial.sql", fake_dir)
        original = S._MIGRATIONS_DIR
        S._MIGRATIONS_DIR = fake_dir
        try:
            S.initialize_database(self.db)
            (fake_dir / "0002_upgrade.sql").write_text(
                "CREATE TABLE upgrade_marker (id INTEGER PRIMARY KEY);",
                encoding="utf-8",
            )
            status = S.initialize_database(self.db)
        finally:
            S._MIGRATIONS_DIR = original

        self.assertEqual(status["latest_applied"], 2)
        backups = list(pathlib.Path(self.tmp).glob("test.bak.*.db"))
        self.assertEqual(len(backups), 1)
        with S.connect(backups[0]) as conn:
            versions = [
                row[0]
                for row in conn.execute(
                    "SELECT version FROM schema_migration ORDER BY version"
                )
            ]
            marker = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='upgrade_marker'"
            ).fetchone()
        self.assertEqual(versions, [1])
        self.assertIsNone(marker)

    def test_unmanaged_database_is_backed_up_before_bootstrap(self):
        unmanaged = sqlite3.connect(self.db)
        unmanaged.execute("CREATE TABLE user_data (value TEXT)")
        unmanaged.execute("INSERT INTO user_data (value) VALUES ('keep-me')")
        unmanaged.commit()
        unmanaged.close()

        S.initialize_database(self.db)

        backups = list(pathlib.Path(self.tmp).glob("test.bak.*.db"))
        self.assertEqual(len(backups), 1)
        with sqlite3.connect(str(backups[0])) as backup:
            value = backup.execute("SELECT value FROM user_data").fetchone()[0]
            migration_table = backup.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migration'"
            ).fetchone()
        self.assertEqual(value, "keep-me")
        self.assertIsNone(migration_table)

    def test_temp_directory_cleanup_pattern(self):
        tmp = tempfile.mkdtemp(prefix="ai_signal_tmp_")
        db = os.path.join(tmp, "scratch.db")
        S.initialize_database(db)
        self.assertTrue(os.path.exists(db))
        shutil.rmtree(tmp, ignore_errors=True)
        self.assertFalse(os.path.exists(tmp))


if __name__ == "__main__":
    unittest.main()
