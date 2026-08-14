"""Integration tests for :mod:`ai_signal.pipeline.collect`."""

import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.pipeline import collect as collect_module  # noqa: E402
from ai_signal.pipeline.collect import CollectionPolicyError, collect_source_once  # noqa: E402
from ai_signal.observability.logging import StructuredLogger  # noqa: E402
from ai_signal.sources.github import GitHubPage  # noqa: E402
from ai_signal.sources.github_fixture import FixtureGitHubClient  # noqa: E402
from ai_signal.storage import sqlite as S  # noqa: E402
from ai_signal.storage.source_repositories import (  # noqa: E402
    SourceCursorRepository,
    SourceRunRepository,
)

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "github"
SCOPE = "github-fixture-v1"


def _config(client, scope_key=SCOPE):
    return {
        "run_mode": "shadow",
        "source": "github",
        "scope_key": scope_key,
        "fixture": True,
        "fixture_sha256": client.fixture_sha256,
    }


def _count(conn, table):
    return conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]


class _FailingClient:
    source_version = "fixture-1"
    fixture_sha256 = "0" * 64

    def fetch(self, cursor):
        raise RuntimeError("simulated client failure")


class _TerminalClient:
    source_version = "fixture-terminal"
    fixture_sha256 = "1" * 64

    def fetch(self, cursor):
        return GitHubPage(
            items=(),
            next_cursor=None,
            source_version=self.source_version,
            fetched_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
        )


class CollectionPipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai_signal_2a_pipe_")
        self.db = os.path.join(self.tmp, "test.db")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _collect(self, fixture="pages.json", scope_key=SCOPE):
        client = FixtureGitHubClient(FIXTURES / fixture)
        return collect_source_once(
            self.db,
            "github",
            "2026-W33",
            scope_key=scope_key,
            client=client,
            config_snapshot=_config(client, scope_key),
        )

    def test_page1_writes_two_signals_and_advances_to_c1(self):
        result = self._collect()
        self.assertEqual(result.status, "success")
        self.assertEqual(result.processed_item_count, 2)
        self.assertTrue(result.cursor_advanced)
        with S.connect(self.db) as conn:
            self.assertEqual(_count(conn, "raw_signal"), 2)
            cursor = SourceCursorRepository(conn).get("github", SCOPE)
            self.assertIsNotNone(cursor)
            self.assertEqual(cursor.cursor, "c1")

    def test_page2_dedups_repeat_and_adds_new(self):
        self._collect()
        result = self._collect()
        self.assertEqual(result.status, "success")
        self.assertEqual(result.processed_item_count, 2)
        with S.connect(self.db) as conn:
            # alpha (repeated) + beta + gamma = 3 distinct raw signals.
            self.assertEqual(_count(conn, "raw_signal"), 3)
            cursor = SourceCursorRepository(conn).get("github", SCOPE)
            self.assertEqual(cursor.cursor, "c2")

    def test_page3_empty_adds_nothing(self):
        self._collect()
        self._collect()
        result = self._collect()
        self.assertEqual(result.status, "success")
        self.assertEqual(result.processed_item_count, 0)
        self.assertFalse(result.cursor_advanced)
        with S.connect(self.db) as conn:
            self.assertEqual(_count(conn, "raw_signal"), 3)
            cursor = SourceCursorRepository(conn).get("github", SCOPE)
            self.assertEqual(cursor.cursor, "c2")
            runs = SourceRunRepository(conn).list_for_collection_run(result.run_id)
            self.assertFalse(runs[0].cursor_advanced)

    def test_success_without_next_cursor_does_not_clear_existing_cursor(self):
        self._collect()
        client = _TerminalClient()
        result = collect_source_once(
            self.db,
            "github",
            "2026-W33",
            scope_key=SCOPE,
            client=client,
            config_snapshot=_config(client, SCOPE),
        )
        self.assertEqual(result.status, "success")
        self.assertFalse(result.cursor_advanced)
        with S.connect(self.db) as conn:
            cursor = SourceCursorRepository(conn).get("github", SCOPE)
            self.assertEqual(cursor.cursor, "c1")

    def test_partial_does_not_advance_cursor(self):
        client = FixtureGitHubClient(FIXTURES / "malformed.json")
        result = collect_source_once(
            self.db, "github", "2026-W33", scope_key=SCOPE, client=client,
            config_snapshot=_config(client, SCOPE),
        )
        self.assertEqual(result.status, "partial")
        self.assertFalse(result.cursor_advanced)
        with S.connect(self.db) as conn:
            self.assertEqual(_count(conn, "raw_signal"), 1)
            self.assertIsNone(SourceCursorRepository(conn).get("github", SCOPE))

    def test_failed_does_not_advance_cursor_and_marks_run_failed(self):
        client = _FailingClient()
        result = collect_source_once(
            self.db,
            "github",
            "2026-W33",
            scope_key=SCOPE,
            client=client,
            config_snapshot={
                "run_mode": "shadow",
                "source": "github",
                "scope_key": SCOPE,
                "fixture": True,
                "fixture_sha256": client.fixture_sha256,
            },
        )
        self.assertEqual(result.status, "failed")
        self.assertFalse(result.cursor_advanced)
        with S.connect(self.db) as conn:
            self.assertIsNone(SourceCursorRepository(conn).get("github", SCOPE))
            row = conn.execute("SELECT status FROM collection_run").fetchone()
            self.assertEqual(row["status"], "failed")

    def test_raw_signal_source_version_preserved(self):
        result = self._collect()
        with S.connect(self.db) as conn:
            raw = conn.execute("SELECT source_version FROM raw_signal LIMIT 1").fetchone()
            self.assertEqual(raw["source_version"], "fixture-1")
            runs = SourceRunRepository(conn).list_for_collection_run(result.run_id)
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0].source_version, "fixture-1")
            self.assertEqual(runs[0].scope_key, SCOPE)

    def test_cursor_and_raw_signals_share_one_transaction(self):
        # Patch cursor advance to fail on the success path: the whole final
        # transaction must roll back, leaving no raw signals, no source run,
        # no cursor, and a failed collection run.
        client = FixtureGitHubClient(FIXTURES / "pages.json")
        original = collect_module.SourceCursorRepository.advance

        def boom(self, cursor):
            raise S.StorageError("injected failure")

        collect_module.SourceCursorRepository.advance = boom
        try:
            with self.assertRaises(S.StorageError):
                collect_source_once(
                    self.db,
                    "github",
                    "2026-W33",
                    scope_key=SCOPE,
                    client=client,
                    config_snapshot=_config(client, SCOPE),
                )
        finally:
            collect_module.SourceCursorRepository.advance = original

        with S.connect(self.db) as conn:
            self.assertEqual(_count(conn, "raw_signal"), 0)
            self.assertEqual(_count(conn, "source_run"), 0)
            self.assertIsNone(SourceCursorRepository(conn).get("github", SCOPE))
            row = conn.execute("SELECT status FROM collection_run").fetchone()
            self.assertEqual(row["status"], "failed")

    def test_unexpected_persistence_error_is_safely_wrapped(self):
        client = FixtureGitHubClient(FIXTURES / "pages.json")
        original = collect_module.SourceRunRepository.insert

        def boom(self, source_run):
            raise RuntimeError("do-not-expose-this-marker")

        collect_module.SourceRunRepository.insert = boom
        try:
            with self.assertRaises(S.StorageError) as cm:
                collect_source_once(
                    self.db,
                    "github",
                    "2026-W33",
                    scope_key=SCOPE,
                    client=client,
                    config_snapshot=_config(client, SCOPE),
                )
        finally:
            collect_module.SourceRunRepository.insert = original

        self.assertNotIn("do-not-expose-this-marker", str(cm.exception))
        with S.connect(self.db) as conn:
            row = conn.execute("SELECT status FROM collection_run").fetchone()
            self.assertEqual(row["status"], "failed")

    def test_config_snapshot_has_no_path_or_secrets(self):
        client = FixtureGitHubClient(FIXTURES / "pages.json")
        result = collect_source_once(
            self.db, "github", "2026-W33", scope_key=SCOPE, client=client,
            config_snapshot=_config(client, SCOPE),
        )
        with S.connect(self.db) as conn:
            row = conn.execute(
                "SELECT config_snapshot FROM collection_run WHERE id = ?", (result.run_id,)
            ).fetchone()
            stored_text = row["config_snapshot"]
            self.assertNotIn(str(FIXTURES), stored_text)
            stored = json.loads(stored_text)
            self.assertEqual(
                set(stored.keys()),
                {"run_mode", "source", "scope_key", "fixture", "fixture_sha256"},
            )

    def test_structured_logs_contain_only_safe_summary_fields(self):
        client = FixtureGitHubClient(FIXTURES / "pages.json")
        stream = io.StringIO()
        logger = StructuredLogger(stage="collect", source="github", stream=stream)
        collect_source_once(
            self.db,
            "github",
            "2026-W33",
            scope_key=SCOPE,
            client=client,
            config_snapshot=_config(client, SCOPE),
            logger=logger,
        )
        output = stream.getvalue()
        self.assertIn("source_collection_started", output)
        self.assertIn("source_collection_completed", output)
        self.assertNotIn(str(FIXTURES), output)
        self.assertNotIn("example.com", output)

    # ----- scope_key isolation regressions ----- #

    def test_multiple_scope_keys_isolate_cursor_progress(self):
        client = FixtureGitHubClient(FIXTURES / "pages.json")
        # scope A first run -> c1
        collect_source_once(
            self.db, "github", "2026-W33", scope_key="scope-a-v1", client=client,
            config_snapshot=_config(client, "scope-a-v1"),
        )
        # scope B first run -> c1 (independent start)
        collect_source_once(
            self.db, "github", "2026-W33", scope_key="scope-b-v1", client=client,
            config_snapshot=_config(client, "scope-b-v1"),
        )
        # scope A second run -> c2
        collect_source_once(
            self.db, "github", "2026-W33", scope_key="scope-a-v1", client=client,
            config_snapshot=_config(client, "scope-a-v1"),
        )
        with S.connect(self.db) as conn:
            repo = SourceCursorRepository(conn)
            self.assertEqual(repo.get("github", "scope-a-v1").cursor, "c2")
            self.assertEqual(repo.get("github", "scope-b-v1").cursor, "c1")
            count = conn.execute("SELECT COUNT(*) FROM source_cursor").fetchone()[0]
            self.assertEqual(count, 2)

    def test_invalid_scope_key_rejected_before_database(self):
        client = FixtureGitHubClient(FIXTURES / "pages.json")
        bad_keys = (
            "",
            "UpperCase",
            "has space",
            "slash/here",
            "a" * 65,
            "raw:query>style",
            "valid-looking-v1\n",
        )
        for bad in bad_keys:
            with self.subTest(scope_key=bad):
                with self.assertRaises(CollectionPolicyError):
                    collect_source_once(
                        self.db, "github", "2026-W33", scope_key=bad, client=client,
                        config_snapshot=_config(client, bad),
                    )
        self.assertFalse(os.path.exists(self.db))

    def test_scope_key_config_mismatch_rejected_before_database(self):
        client = FixtureGitHubClient(FIXTURES / "pages.json")
        with self.assertRaises(CollectionPolicyError):
            collect_source_once(
                self.db,
                "github",
                "2026-W33",
                scope_key="scope-a-v1",
                client=client,
                config_snapshot=_config(client, "scope-b-v1"),
            )
        self.assertFalse(os.path.exists(self.db))


if __name__ == "__main__":
    unittest.main()
