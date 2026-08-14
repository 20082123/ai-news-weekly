"""Unit tests for :mod:`ai_signal.sources.github`."""

import pathlib
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.sources.github import (  # noqa: E402
    WARN_CLIENT_FAILURE,
    WARN_MALFORMED_ITEM,
    GitHubPage,
    GitHubSource,
)

FETCHED_AT = datetime(2026, 8, 13, 0, 0, 0, tzinfo=timezone.utc)


class _FakeClient:
    source_version = "fake-1"

    def __init__(self, page=None, exc=None):
        self._page = page
        self._exc = exc

    def fetch(self, cursor):
        if self._exc is not None:
            raise self._exc
        return self._page


def _good_item(item_id=101, **overrides):
    item = {
        "id": item_id,
        "full_name": "example-org/example-%s" % item_id,
        "html_url": "https://example.com/example-org/example-%s" % item_id,
        "description": "an example repository",
        "language": "Python",
        "stargazers_count": 1,
        "forks_count": 0,
        "topics": ["example"],
        "pushed_at": "2026-08-01T00:00:00+00:00",
        "updated_at": "2026-08-02T00:00:00+00:00",
    }
    item.update(overrides)
    return item


class GitHubSourceParseTest(unittest.TestCase):
    def test_parses_valid_items(self):
        page = GitHubPage(
            items=(_good_item(101), _good_item(102)),
            next_cursor="c1",
            source_version="fake-1",
            fetched_at=FETCHED_AT,
        )
        batch = GitHubSource(_FakeClient(page=page)).collect(None, {})
        self.assertEqual(batch.status, "success")
        self.assertEqual(len(batch.items), 2)
        self.assertEqual(batch.items[0].external_id, "101")
        self.assertEqual(batch.next_cursor, "c1")
        self.assertEqual(batch.source_version, "fake-1")

    def test_payload_keeps_whitelist_only(self):
        raw = _good_item(101)
        raw["unknown_field"] = "dropped"
        raw["owner_login"] = "dropped-too"
        page = GitHubPage(items=(raw,), next_cursor=None, source_version="fake-1", fetched_at=FETCHED_AT)
        batch = GitHubSource(_FakeClient(page=page)).collect(None, {})
        self.assertEqual(batch.status, "success")
        payload = batch.items[0].payload
        self.assertNotIn("unknown_field", payload)
        self.assertNotIn("owner_login", payload)
        for key in (
            "id",
            "full_name",
            "html_url",
            "description",
            "language",
            "stargazers_count",
            "forks_count",
            "topics",
            "pushed_at",
            "updated_at",
        ):
            self.assertIn(key, payload)

    def test_malformed_item_makes_batch_partial(self):
        bad = {"id": 999}  # missing required fields
        page = GitHubPage(
            items=(_good_item(101), bad),
            next_cursor="c1",
            source_version="fake-1",
            fetched_at=FETCHED_AT,
        )
        batch = GitHubSource(_FakeClient(page=page)).collect(None, {})
        self.assertEqual(batch.status, "partial")
        self.assertEqual(len(batch.items), 1)
        self.assertEqual(batch.warnings, ("%s:1" % WARN_MALFORMED_ITEM,))

    def test_client_failure_makes_batch_failed(self):
        batch = GitHubSource(_FakeClient(exc=RuntimeError("boom"))).collect(None, {})
        self.assertEqual(batch.status, "failed")
        self.assertEqual(batch.warnings, (WARN_CLIENT_FAILURE,))
        # The exception text must not leak into the batch.
        self.assertNotIn("boom", " ".join(batch.warnings))

    def test_warnings_are_stable_and_safe(self):
        bad = {"id": 501}  # missing required fields
        page = GitHubPage(items=(bad,), next_cursor=None, source_version="fake-1", fetched_at=FETCHED_AT)
        batch = GitHubSource(_FakeClient(page=page)).collect(None, {})
        self.assertEqual(batch.warnings, ("%s:0" % WARN_MALFORMED_ITEM,))
        warning = batch.warnings[0]
        self.assertNotIn("example.com", warning)
        self.assertNotIn("http", warning)

    def test_rejects_non_https_url(self):
        raw = _good_item(101)
        raw["html_url"] = "http://example.com/insecure"
        page = GitHubPage(items=(raw,), next_cursor=None, source_version="fake-1", fetched_at=FETCHED_AT)
        batch = GitHubSource(_FakeClient(page=page)).collect(None, {})
        self.assertEqual(batch.status, "partial")
        self.assertEqual(batch.warnings, ("%s:0" % WARN_MALFORMED_ITEM,))

    def test_rejects_https_url_without_host(self):
        raw = _good_item(101, html_url="https://")
        page = GitHubPage(
            items=(raw,),
            next_cursor=None,
            source_version="fake-1",
            fetched_at=FETCHED_AT,
        )
        batch = GitHubSource(_FakeClient(page=page)).collect(None, {})
        self.assertEqual(batch.status, "partial")

    def test_rejects_url_with_embedded_credentials(self):
        raw = _good_item(101, html_url="https://user:pass@example.com/repo")
        page = GitHubPage(
            items=(raw,),
            next_cursor=None,
            source_version="fake-1",
            fetched_at=FETCHED_AT,
        )
        batch = GitHubSource(_FakeClient(page=page)).collect(None, {})
        self.assertEqual(batch.status, "partial")

    def test_accepts_nullable_github_fields(self):
        raw = _good_item(101, description=None, language=None, pushed_at=None)
        page = GitHubPage(
            items=(raw,),
            next_cursor=None,
            source_version="fake-1",
            fetched_at=FETCHED_AT,
        )
        batch = GitHubSource(_FakeClient(page=page)).collect(None, {})
        self.assertEqual(batch.status, "success")
        self.assertIsNone(batch.items[0].payload["description"])
        self.assertIsNone(batch.items[0].payload["language"])
        self.assertIsNone(batch.items[0].payload["pushed_at"])

    def test_drops_sensitive_payload_fields(self):
        raw = _good_item(101)
        raw["token"] = "supersecret"
        raw["authorization"] = "Bearer abc"
        page = GitHubPage(items=(raw,), next_cursor=None, source_version="fake-1", fetched_at=FETCHED_AT)
        batch = GitHubSource(_FakeClient(page=page)).collect(None, {})
        self.assertEqual(batch.status, "success")
        payload = batch.items[0].payload
        self.assertNotIn("token", payload)
        self.assertNotIn("authorization", payload)

    def test_clock_is_injectable(self):
        times = iter(
            [
                datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
                datetime(2026, 1, 2, 0, 0, 0, tzinfo=timezone.utc),
            ]
        )
        page = GitHubPage(items=(_good_item(101),), next_cursor=None, source_version="fake-1", fetched_at=FETCHED_AT)
        batch = GitHubSource(_FakeClient(page=page), clock=lambda: next(times)).collect(None, {})
        self.assertEqual(batch.started_at, datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(batch.finished_at, datetime(2026, 1, 2, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
