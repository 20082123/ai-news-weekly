"""Unit tests for :mod:`ai_signal.sources.github_fixture`."""

import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.sources.github_fixture import FixtureError, FixtureGitHubClient  # noqa: E402

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "github"


class FixtureClientTest(unittest.TestCase):
    def test_three_cursors(self):
        client = FixtureGitHubClient(FIXTURES / "pages.json")
        page0 = client.fetch(None)
        self.assertEqual(len(page0.items), 2)
        self.assertEqual(page0.next_cursor, "c1")
        page1 = client.fetch("c1")
        self.assertEqual(len(page1.items), 2)
        self.assertEqual(page1.next_cursor, "c2")
        page2 = client.fetch("c2")
        self.assertEqual(page2.items, ())
        self.assertEqual(page2.next_cursor, "c2")

    def test_unknown_cursor_raises(self):
        client = FixtureGitHubClient(FIXTURES / "pages.json")
        with self.assertRaises(FixtureError) as cm:
            client.fetch("does-not-exist")
        self.assertNotIn(str(FIXTURES), str(cm.exception))

    def test_source_version_and_sha(self):
        client = FixtureGitHubClient(FIXTURES / "pages.json")
        self.assertEqual(client.source_version, "fixture-1")
        self.assertEqual(len(client.fixture_sha256), 64)
        try:
            int(client.fixture_sha256, 16)
        except ValueError:
            self.fail("fixture_sha256 must be hex")

    def test_invalid_json_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "bad.json"
            path.write_text("{not valid json", encoding="utf-8")
            with self.assertRaises(FixtureError):
                FixtureGitHubClient(path)

    def test_bad_schema_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "bad.json"
            path.write_text(json.dumps({"source_version": "x"}), encoding="utf-8")
            with self.assertRaises(FixtureError):
                FixtureGitHubClient(path)

    def test_error_does_not_leak_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = pathlib.Path(tmp) / "marker_path_xyz" / "bad.json"
            marker.parent.mkdir()
            marker.write_text("{bad", encoding="utf-8")
            with self.assertRaises(FixtureError) as cm:
                FixtureGitHubClient(marker)
            self.assertNotIn("marker_path_xyz", str(cm.exception))

    def test_naive_timestamp_is_rejected_during_initialization(self):
        fixture = {
            "source_version": "fixture-test",
            "pages": [
                {
                    "cursor_in": None,
                    "cursor_out": "c1",
                    "fetched_at": "2026-08-13T00:00:00",
                    "items": [],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "naive.json"
            path.write_text(json.dumps(fixture), encoding="utf-8")
            with self.assertRaises(FixtureError):
                FixtureGitHubClient(path)

    def test_duplicate_input_cursor_is_rejected(self):
        page = {
            "cursor_in": None,
            "cursor_out": "c1",
            "fetched_at": "2026-08-13T00:00:00+00:00",
            "items": [],
        }
        fixture = {"source_version": "fixture-test", "pages": [page, dict(page)]}
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "duplicate.json"
            path.write_text(json.dumps(fixture), encoding="utf-8")
            with self.assertRaises(FixtureError):
                FixtureGitHubClient(path)

    def test_missing_page_key_is_rejected_safely(self):
        fixture = {
            "source_version": "fixture-test",
            "pages": [
                {
                    "cursor_out": "c1",
                    "fetched_at": "2026-08-13T00:00:00+00:00",
                    "items": [],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "missing.json"
            path.write_text(json.dumps(fixture), encoding="utf-8")
            with self.assertRaises(FixtureError) as cm:
                FixtureGitHubClient(path)
            self.assertEqual(
                str(cm.exception), "fixture page is missing required fields"
            )


if __name__ == "__main__":
    unittest.main()
