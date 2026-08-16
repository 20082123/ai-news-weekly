"""Integration tests for the phase 2D3-B official announcement pipeline."""

import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.discovery.official_catalog import OfficialSourceSpec  # noqa: E402
from ai_signal.pipeline.collect import CollectionPolicyError  # noqa: E402
from ai_signal.pipeline.official_discovery import (  # noqa: E402
    collect_official_announcements,
)
from ai_signal.sources.github_rest import HttpResponse  # noqa: E402
from ai_signal.sources.official_http import (  # noqa: E402
    ERR_NETWORK_FAILURE,
    OfficialHttpError,
)
from ai_signal.storage import sqlite as S  # noqa: E402

RSS_BODY = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<rss version="2.0"><channel><title>Test</title>'
    "<item><title>公告一</title><link>https://example.com/a</link>"
    "<pubDate>Wed, 13 Aug 2026 09:00:00 GMT</pubDate>"
    "<description>&lt;p&gt;这是&lt;b&gt;摘要&lt;/b&gt;正文&lt;/p&gt;</description></item>"
    "<item><title>公告二</title><link>http://unsafe.example/b</link>"
    "<pubDate>Thu, 14 Aug 2026 09:00:00 GMT</pubDate>"
    "<description>unsafe link entry</description></item>"
    "<item><title>无链接</title><pubDate>Fri, 15 Aug 2026 09:00:00 GMT</pubDate>"
    "<description>no link</description></item>"
    "</channel></rss>"
).encode("utf-8")

ATOM_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<feed xmlns="http://www.w3.org/2005/Atom"><title>F</title>'
    "<entry><title>Atom 公告</title>"
    '<link href="https://example.com/atom-a"/>'
    "<published>2026-08-12T00:00:00Z</published>"
    "<summary>&lt;p&gt;atom 摘要&lt;/p&gt;</summary></entry>"
    "</feed>"
).encode("utf-8")


class _FakeOfficialTransport:
    def __init__(self, body_by_url=None, failures=()):
        self.body_by_url = body_by_url or {}
        self.failures = set(failures)
        self.calls = []

    def get(self, url, headers, timeout_seconds, max_response_bytes):
        self.calls.append(url)
        if url in self.failures:
            # Mirror the real transport: failures are classified into stable
            # codes, never raw exceptions.
            raise OfficialHttpError(ERR_NETWORK_FAILURE)
        body = self.body_by_url.get(url)
        if body is None:
            raise RuntimeError("unexpected url")
        return HttpResponse(
            status=200,
            headers={"Content-Type": "application/rss+xml"},
            body=body,
            final_url=url,
        )


class OfficialPipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai_official_")
        self.db = os.path.join(self.tmp, "test.db")
        S.initialize_database(self.db)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _patch_sources(self, sources):
        patcher = mock.patch(
            "ai_signal.discovery.official_catalog.OFFICIAL_SOURCES", sources
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _collect(self, transport):
        conn = S._open(self.db)
        conn.execute("BEGIN")
        result = collect_official_announcements(
            conn, allow_network=True, transport_factory=lambda: transport
        )
        conn.execute("COMMIT")
        conn.close()
        return result

    def test_collect_rss_skips_unsafe_and_missing_links(self):
        source = OfficialSourceSpec(
            name="test-feed",
            feed_url="https://example.com/feed.xml",
            site_url="https://example.com",
        )
        self._patch_sources((source,))
        transport = _FakeOfficialTransport(
            body_by_url={"https://example.com/feed.xml": RSS_BODY}
        )
        result = self._collect(transport)
        self.assertEqual(result.sources_attempted, 1)
        self.assertEqual(result.sources_failed, 0)
        self.assertEqual(result.created, 1)  # only the safe entry
        self.assertEqual(result.skipped_unsafe, 1)  # http link dropped
        conn = S._open(self.db)
        try:
            rows = conn.execute(
                "SELECT title, summary, status FROM "
                "official_announcement_candidate"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "公告一")
        self.assertEqual(rows[0]["status"], "new")
        self.assertIn("摘要", rows[0]["summary"])
        self.assertNotIn("<b>", rows[0]["summary"])

    def test_atom_feed_parsed(self):
        source = OfficialSourceSpec(
            name="test-atom",
            feed_url="https://example.com/atom.xml",
            site_url="https://example.com",
        )
        self._patch_sources((source,))
        transport = _FakeOfficialTransport(
            body_by_url={"https://example.com/atom.xml": ATOM_BODY}
        )
        result = self._collect(transport)
        self.assertEqual(result.created, 1)
        conn = S._open(self.db)
        try:
            title = conn.execute(
                "SELECT title FROM official_announcement_candidate"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(title, "Atom 公告")

    def test_rerun_idempotent(self):
        source = OfficialSourceSpec(
            name="test-feed",
            feed_url="https://example.com/feed.xml",
            site_url="https://example.com",
        )
        self._patch_sources((source,))
        transport = _FakeOfficialTransport(
            body_by_url={"https://example.com/feed.xml": RSS_BODY}
        )
        first = self._collect(transport)
        second = self._collect(transport)
        self.assertEqual(first.created, 1)
        self.assertEqual(second.created, 0)
        self.assertEqual(second.existing, 1)

    def test_failed_source_degrades_visibly(self):
        source = OfficialSourceSpec(
            name="test-feed",
            feed_url="https://example.com/feed.xml",
            site_url="https://example.com",
        )
        self._patch_sources((source,))
        transport = _FakeOfficialTransport(
            body_by_url={"https://example.com/feed.xml": RSS_BODY},
            failures={"https://example.com/feed.xml"},
        )
        result = self._collect(transport)
        self.assertEqual(result.sources_failed, 1)
        self.assertEqual(result.created, 0)

    def test_no_network_refused(self):
        conn = S._open(self.db)
        with self.assertRaises(CollectionPolicyError):
            collect_official_announcements(conn)
        conn.close()


if __name__ == "__main__":
    unittest.main()
