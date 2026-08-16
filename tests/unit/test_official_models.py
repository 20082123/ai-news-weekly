"""Unit tests for the phase 2D3-B official sensor (models + RSS client)."""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.discovery.official_catalog import (  # noqa: E402
    OFFICIAL_SOURCES,
    OfficialSourceSpec,
)
from ai_signal.domain.models import (  # noqa: E402
    OfficialAnnouncementCandidate,
)
from ai_signal.sources.github_rest import HttpResponse  # noqa: E402
from ai_signal.sources.official_http import (  # noqa: E402
    ERR_INVALID_CONTENT,
    OfficialHttpError,
    OfficialRssClient,
)


class OfficialCandidateModelTest(unittest.TestCase):
    def test_identity_deterministic_over_name_and_url(self):
        a = OfficialAnnouncementCandidate(
            source_name="google-ai-blog",
            title="标题",
            url="https://blog.google/technology/ai/x/",
            published_at="2026-08-13",
            summary="摘要",
        )
        b = OfficialAnnouncementCandidate(
            source_name="google-ai-blog",
            title="标题变了也没关系",
            url="https://blog.google/technology/ai/x/",
            published_at="2026-08-13",
            summary="摘要",
        )
        self.assertEqual(a.id, b.id)
        self.assertEqual(len(a.id), 64)

    def test_validation(self):
        with self.assertRaises(ValueError):
            OfficialAnnouncementCandidate(
                source_name="x", title="t", url="http://insecure/x",
                published_at="d", summary="s",
            )
        with self.assertRaises(ValueError):
            OfficialAnnouncementCandidate(
                source_name="x", title="", url="https://a.b/c",
                published_at="d", summary="s",
            )
        with self.assertRaises(ValueError):
            OfficialAnnouncementCandidate(
                source_name="x", title="t", url="https://a.b/c",
                published_at="d", summary="s", status="nope",
            )


class OfficialCatalogTest(unittest.TestCase):
    def test_sources_are_clean_https_and_unique(self):
        names = [s.name for s in OFFICIAL_SOURCES]
        self.assertEqual(len(names), len(set(names)))
        for source in OFFICIAL_SOURCES:
            self.assertTrue(source.feed_url.startswith("https://"))
            self.assertTrue(source.site_url.startswith("https://"))

    def test_bad_source_rejected(self):
        with self.assertRaises(ValueError):
            OfficialSourceSpec(
                name="bad source", feed_url="http://x/feed", site_url="http://x"
            )


class _FakeTransport:
    def __init__(self, content_type="application/rss+xml", body=b"",
                 final_url="https://example.com/feed.xml"):
        self.content_type = content_type
        self.body = body
        self.final_url = final_url

    def get(self, url, headers, timeout_seconds, max_response_bytes):
        return HttpResponse(
            status=200,
            headers={"Content-Type": self.content_type},
            body=self.body,
            final_url=self.final_url,
        )


class OfficialRssClientTest(unittest.TestCase):
    def test_fetch_returns_raw_xml(self):
        body = b"<rss><channel><item><title>x</title></item></channel></rss>"
        client = OfficialRssClient(_FakeTransport(body=body))
        self.assertEqual(client.fetch("https://example.com/feed.xml"), body)

    def test_non_xml_content_rejected(self):
        client = OfficialRssClient(_FakeTransport(content_type="text/html"))
        with self.assertRaises(OfficialHttpError) as ctx:
            client.fetch("https://example.com/feed.xml")
        self.assertEqual(ctx.exception.code, ERR_INVALID_CONTENT)

    def test_unsafe_url_rejected_before_transport(self):
        client = OfficialRssClient(_FakeTransport())
        with self.assertRaises(OfficialHttpError):
            client.fetch("http://example.com/feed.xml")


if __name__ == "__main__":
    unittest.main()
