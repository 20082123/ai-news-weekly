"""Unit tests for the phase 2D3 official-page fetch client (offline)."""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.sources.github_rest import HttpResponse  # noqa: E402
from ai_signal.sources.official_http import (  # noqa: E402
    ERR_INVALID_CONTENT,
    ERR_TOO_LARGE,
    ERR_UNSAFE_URL,
    OfficialHttpClient,
    OfficialHttpError,
)


class _FakeTransport:
    def __init__(self, status=200, content_type="text/html",
                 body=b"", final_url="https://example.com/page"):
        self.status = status
        self.content_type = content_type
        self.body = body
        self.final_url = final_url
        self.calls = []

    def get(self, url, headers, timeout_seconds, max_response_bytes):
        self.calls.append(url)
        return HttpResponse(
            status=self.status,
            headers={"Content-Type": self.content_type},
            body=self.body,
            final_url=self.final_url,
        )


def _client(transport):
    return OfficialHttpClient(transport, timeout_seconds=10, text_cap=500)


class OfficialHttpClientTest(unittest.TestCase):
    def test_fetch_strips_tags_and_controls(self):
        transport = _FakeTransport(
            body=(
                "<html><head><script>evil()</script><style>x{}</style></head>"
                "<body><h1>Title</h1>  正文\x00内容&nbsp;结束</body></html>"
            ).encode("utf-8"),
        )
        text = _client(transport).fetch("https://example.com/page")
        self.assertIn("Title", text)
        self.assertIn("正文", text)
        self.assertNotIn("evil()", text)
        self.assertNotIn("<h1>", text)
        self.assertEqual(len(transport.calls), 1)

    def test_unsafe_url_rejected_before_transport(self):
        transport = _FakeTransport(body=b"x")
        for bad in ("http://example.com/x", "https://user:pw@example.com/x",
                    "javascript:alert(1)"):
            with self.assertRaises(OfficialHttpError) as ctx:
                _client(transport).fetch(bad)
            self.assertEqual(ctx.exception.code, ERR_UNSAFE_URL)
        self.assertEqual(transport.calls, [])

    def test_final_url_downgrade_rejected(self):
        transport = _FakeTransport(final_url="http://evil.example/x")
        with self.assertRaises(OfficialHttpError) as ctx:
            _client(transport).fetch("https://example.com/page")
        self.assertEqual(ctx.exception.code, ERR_UNSAFE_URL)

    def test_oversized_body_rejected(self):
        transport = _FakeTransport(body=b"a" * 600)
        with self.assertRaises(OfficialHttpError) as ctx:
            OfficialHttpClient(
                transport, timeout_seconds=10, max_response_bytes=100, text_cap=500
            ).fetch("https://example.com/page")
        self.assertEqual(ctx.exception.code, ERR_TOO_LARGE)

    def test_non_html_content_rejected(self):
        transport = _FakeTransport(content_type="application/json", body=b"{}")
        with self.assertRaises(OfficialHttpError) as ctx:
            _client(transport).fetch("https://example.com/page")
        self.assertEqual(ctx.exception.code, ERR_INVALID_CONTENT)

    def test_text_capped(self):
        transport = _FakeTransport(body=b"word " * 500)
        text = _client(transport).fetch("https://example.com/page")
        self.assertLessEqual(len(text), 500)


if __name__ == "__main__":
    unittest.main()
