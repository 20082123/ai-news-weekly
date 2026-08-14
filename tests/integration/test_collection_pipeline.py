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
from ai_signal.sources.github_rest import (  # noqa: E402
    GitHubRestClient,
    GitHubSearchSpec,
    HttpResponse,
)
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
        "adapter_kind": "fixture",
        "fixture": True,
        "fixture_sha256": client.fixture_sha256,
    }


def _rest_config(spec, scope_key):
    return {
        "run_mode": "shadow",
        "source": "github",
        "scope_key": scope_key,
        "adapter_kind": "github-rest-v1",
        "query_sha256": spec.query_sha256,
        "sort": spec.sort,
        "order": spec.order,
        "per_page": spec.per_page,
        "max_pages": spec.max_pages,
    }


class _FakeRestTransport:
    def __init__(self, response):
        self._response = response
        self.calls = 0
        self.requested_pages = []

    def get(self, url, headers, timeout_seconds, max_response_bytes):
        self.calls += 1
        from urllib.parse import parse_qs, urlparse

        self.requested_pages.append(parse_qs(urlparse(url).query).get("page", ["1"])[0])
        return self._response


def _rest_item(item_id):
    return {
        "id": item_id,
        "full_name": "example-org/example-%d" % item_id,
        "html_url": "https://example.com/example-org/example-%d" % item_id,
        "description": "an example repository",
        "language": "Python",
        "stargazers_count": 2,
        "forks_count": 0,
        "topics": ["example"],
        "pushed_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-02T00:00:00Z",
    }


def _rest_response(items, total_count=None, status=200):
    if total_count is None:
        total_count = len(items)
    body = json.dumps({"total_count": total_count, "items": items}).encode("utf-8")
    return HttpResponse(
        status=status,
        headers={"Content-Type": "application/json"},
        body=body,
        final_url="https://api.github.com/search/repositories",
    )


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
                "adapter_kind": "fixture",
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
                {
                    "run_mode",
                    "source",
                    "scope_key",
                    "adapter_kind",
                    "fixture",
                    "fixture_sha256",
                },
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


    # ----- live (github-rest-v1) adapter regressions ----- #

    def test_live_adapter_writes_records_and_advances_to_next_page(self):
        spec = GitHubSearchSpec(query="topic:ai-agent", per_page=10, max_pages=2)
        client = GitHubRestClient(spec, _FakeRestTransport(_rest_response([_rest_item(1), _rest_item(2)], 15)))
        result = collect_source_once(
            self.db, "github", "2026-W33", scope_key="ai-agents-v1",
            client=client, config_snapshot=_rest_config(spec, "ai-agents-v1"),
        )
        self.assertEqual(result.status, "success")
        self.assertTrue(result.cursor_advanced)
        with S.connect(self.db) as conn:
            self.assertEqual(_count(conn, "raw_signal"), 2)
            self.assertEqual(_count(conn, "source_run"), 1)
            cursor = SourceCursorRepository(conn).get("github", "ai-agents-v1")
            self.assertEqual(cursor.cursor, "page:2")

    def test_live_adapter_config_has_no_raw_query(self):
        spec = GitHubSearchSpec(query="topic:secret-marker-query")
        client = GitHubRestClient(spec, _FakeRestTransport(_rest_response([_rest_item(1)], 1)))
        result = collect_source_once(
            self.db, "github", "2026-W33", scope_key="ai-agents-v1",
            client=client, config_snapshot=_rest_config(spec, "ai-agents-v1"),
        )
        with S.connect(self.db) as conn:
            row = conn.execute(
                "SELECT config_snapshot FROM collection_run WHERE id = ?", (result.run_id,)
            ).fetchone()
            stored = row["config_snapshot"]
            self.assertNotIn("topic:secret-marker-query", stored)
            self.assertIn(spec.query_sha256, stored)
            cfg = json.loads(stored)
            self.assertEqual(cfg["adapter_kind"], "github-rest-v1")
            self.assertNotIn("fixture", cfg)
            self.assertNotIn("fixture_sha256", cfg)
            self.assertEqual(cfg["query_sha256"], spec.query_sha256)

    def test_live_adapter_same_scope_continues_next_page(self):
        spec = GitHubSearchSpec(query="topic:ai-agent", per_page=10, max_pages=3)
        client = GitHubRestClient(spec, _FakeRestTransport(_rest_response([_rest_item(1)], 25)))
        first = collect_source_once(
            self.db, "github", "2026-W33", scope_key="ai-agents-v1",
            client=client, config_snapshot=_rest_config(spec, "ai-agents-v1"),
        )
        self.assertTrue(first.cursor_advanced)
        second = collect_source_once(
            self.db, "github", "2026-W33", scope_key="ai-agents-v1",
            client=client, config_snapshot=_rest_config(spec, "ai-agents-v1"),
        )
        self.assertEqual(second.status, "success")
        with S.connect(self.db) as conn:
            cursor = SourceCursorRepository(conn).get("github", "ai-agents-v1")
            self.assertEqual(cursor.cursor, "page:3")

    def test_live_adapter_scopes_are_independent(self):
        spec = GitHubSearchSpec(query="topic:ai-agent", per_page=10, max_pages=2)
        client = GitHubRestClient(spec, _FakeRestTransport(_rest_response([_rest_item(1)], 15)))
        collect_source_once(
            self.db, "github", "2026-W33", scope_key="scope-a-v1",
            client=client, config_snapshot=_rest_config(spec, "scope-a-v1"),
        )
        collect_source_once(
            self.db, "github", "2026-W33", scope_key="scope-b-v1",
            client=client, config_snapshot=_rest_config(spec, "scope-b-v1"),
        )
        with S.connect(self.db) as conn:
            repo = SourceCursorRepository(conn)
            self.assertEqual(repo.get("github", "scope-a-v1").cursor, "page:2")
            self.assertEqual(repo.get("github", "scope-b-v1").cursor, "page:2")
            self.assertEqual(_count(conn, "source_cursor"), 2)

    def test_live_adapter_max_pages_one_rerecans_periodically(self):
        # max_pages=1 with plenty of results: cursor wraps to page:1 (not a
        # terminal state). Each run is a real page-1 request; SQLite dedup
        # keeps raw_signal stable and the cursor stays at page:1 with no
        # further advance.
        spec = GitHubSearchSpec(query="topic:ai-agent", per_page=10, max_pages=1)
        transport = _FakeRestTransport(_rest_response([_rest_item(1)], 100))
        client = GitHubRestClient(spec, transport)
        first = collect_source_once(
            self.db, "github", "2026-W33", scope_key="ai-agents-v1",
            client=client, config_snapshot=_rest_config(spec, "ai-agents-v1"),
        )
        self.assertEqual(first.status, "success")
        self.assertTrue(first.cursor_advanced)  # None -> page:1 is an advance
        self.assertEqual(transport.calls, 1)
        with S.connect(self.db) as conn:
            raw_after_first = _count(conn, "raw_signal")
            cursor = SourceCursorRepository(conn).get("github", "ai-agents-v1")
            self.assertEqual(cursor.cursor, "page:1")

        second = collect_source_once(
            self.db, "github", "2026-W33", scope_key="ai-agents-v1",
            client=client, config_snapshot=_rest_config(spec, "ai-agents-v1"),
        )
        self.assertEqual(second.status, "success")
        self.assertEqual(transport.calls, 2)  # a real HTTP call happened
        self.assertEqual(transport.requested_pages, ["1", "1"])
        self.assertFalse(second.cursor_advanced)  # page:1 -> page:1, no change
        with S.connect(self.db) as conn:
            self.assertEqual(_count(conn, "raw_signal"), raw_after_first)
            cursor = SourceCursorRepository(conn).get("github", "ai-agents-v1")
            self.assertEqual(cursor.cursor, "page:1")

    def test_live_adapter_max_pages_three_cycles_to_page_one(self):
        # max_pages=3 with plenty of results: 1 -> 2 -> 3 -> wrap to 1, and the
        # next run re-scans page 1 (not stuck repeating page 3).
        spec = GitHubSearchSpec(query="topic:ai-agent", per_page=10, max_pages=3)
        transport = _FakeRestTransport(_rest_response([_rest_item(1)], 25))
        client = GitHubRestClient(spec, transport)
        for expected_cursor in ("page:2", "page:3", "page:1", "page:2"):
            result = collect_source_once(
                self.db, "github", "2026-W33", scope_key="ai-agents-v1",
                client=client, config_snapshot=_rest_config(spec, "ai-agents-v1"),
            )
            self.assertEqual(result.status, "success")
            with S.connect(self.db) as conn:
                cursor = SourceCursorRepository(conn).get("github", "ai-agents-v1")
                self.assertEqual(cursor.cursor, expected_cursor)
        self.assertEqual(transport.requested_pages, ["1", "2", "3", "1"])

    def test_live_adapter_rate_limited_does_not_advance_cursor(self):
        spec = GitHubSearchSpec(query="topic:ai-agent")
        client = GitHubRestClient(
            spec,
            _FakeRestTransport(
                HttpResponse(
                    status=429,
                    headers={"Content-Type": "application/json", "X-RateLimit-Remaining": "0"},
                    body=b"{}",
                    final_url="https://api.github.com/search/repositories",
                )
            ),
        )
        result = collect_source_once(
            self.db, "github", "2026-W33", scope_key="ai-agents-v1",
            client=client, config_snapshot=_rest_config(spec, "ai-agents-v1"),
        )
        self.assertEqual(result.status, "failed")
        self.assertFalse(result.cursor_advanced)
        with S.connect(self.db) as conn:
            self.assertIsNone(SourceCursorRepository(conn).get("github", "ai-agents-v1"))


if __name__ == "__main__":
    unittest.main()
