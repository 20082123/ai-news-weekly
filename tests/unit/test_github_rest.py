"""Unit tests for :mod:`ai_signal.sources.github_rest`.

No test in this file ever performs a real network call: a fake transport is
injected, and the production ``UrllibTransport`` is never instantiated.
"""

import json
import pathlib
import sys
import unittest
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.sources.github import GitHubClientError, GitHubSource  # noqa: E402
from ai_signal.sources.github_rest import (  # noqa: E402
    GitHubRestClient,
    GitHubSearchSpec,
    HttpResponse,
)

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "github"
_OK_FINAL = "https://api.github.com/search/repositories"


def _item(item_id=1):
    return {
        "id": item_id,
        "full_name": "example-org/example-%d" % item_id,
        "html_url": "https://example.com/example-org/example-%d" % item_id,
        "description": "an example repository",
        "language": "Python",
        "stargazers_count": 1,
        "forks_count": 0,
        "topics": ["example"],
        "pushed_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-02T00:00:00Z",
    }


def _body(items, total_count=None):
    if total_count is None:
        total_count = len(items)
    return json.dumps({"total_count": total_count, "items": items}).encode("utf-8")


def _ok(body, final_url=_OK_FINAL, content_type="application/json"):
    return HttpResponse(
        status=200,
        headers={"Content-Type": content_type},
        body=body,
        final_url=final_url,
    )


def _status(status, headers=None, body=b"{}"):
    merged = {"Content-Type": "application/json"}
    if headers:
        merged.update(headers)
    return HttpResponse(status=status, headers=merged, body=body, final_url=_OK_FINAL)


class _FakeTransport:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.calls = 0
        self.requested_pages = []
        self.last_url = None
        self.last_headers = None
        self.last_timeout = None
        self.last_max_bytes = None

    def get(self, url, headers, timeout_seconds, max_response_bytes):
        self.calls += 1
        self.last_url = url
        qs = parse_qs(urlparse(url).query)
        self.requested_pages.append(qs.get("page", ["1"])[0])
        self.last_headers = dict(headers)
        self.last_timeout = timeout_seconds
        self.last_max_bytes = max_response_bytes
        if self._error is not None:
            raise self._error
        return self._response


def _client(spec, transport):
    return GitHubRestClient(spec, transport)


class SearchSpecTest(unittest.TestCase):
    def test_valid_defaults(self):
        spec = GitHubSearchSpec(query="topic:example")
        self.assertEqual(spec.sort, "updated")
        self.assertEqual(spec.order, "desc")
        self.assertEqual(spec.per_page, 10)
        self.assertEqual(spec.max_pages, 1)
        self.assertEqual(spec.source_version, "github-rest-v1")

    def test_empty_or_blank_query_rejected(self):
        for bad in ("", "   ", "\t"):
            with self.subTest(query=bad):
                with self.assertRaises(ValueError):
                    GitHubSearchSpec(query=bad)

    def test_too_long_query_rejected(self):
        with self.assertRaises(ValueError):
            GitHubSearchSpec(query="a" * 257)
        # 256 is allowed.
        GitHubSearchSpec(query="a" * 256)

    def test_control_characters_rejected(self):
        for bad in ("a\rb", "a\nb", "a\0b", "a\tb", "a\x7fb"):
            with self.subTest(query=bad):
                with self.assertRaises(ValueError):
                    GitHubSearchSpec(query=bad)

    def test_invalid_sort_and_order(self):
        with self.assertRaises(ValueError):
            GitHubSearchSpec(query="x", sort="forks")
        with self.assertRaises(ValueError):
            GitHubSearchSpec(query="x", order="sideways")

    def test_per_page_bounds(self):
        for bad in (0, 26, -1):
            with self.subTest(per_page=bad):
                with self.assertRaises(ValueError):
                    GitHubSearchSpec(query="x", per_page=bad)
        GitHubSearchSpec(query="x", per_page=1)
        GitHubSearchSpec(query="x", per_page=25)

    def test_max_pages_bounds(self):
        for bad in (0, 4, -1):
            with self.subTest(max_pages=bad):
                with self.assertRaises(ValueError):
                    GitHubSearchSpec(query="x", max_pages=bad)
        GitHubSearchSpec(query="x", max_pages=3)

    def test_query_sha256_is_deterministic(self):
        a = GitHubSearchSpec(query="topic:ai-agent", per_page=10, max_pages=1)
        b = GitHubSearchSpec(query="topic:ai-agent", per_page=10, max_pages=1)
        self.assertEqual(a.query_sha256, b.query_sha256)
        self.assertEqual(len(a.query_sha256), 64)

    def test_different_query_different_hash(self):
        a = GitHubSearchSpec(query="topic:ai-agent")
        b = GitHubSearchSpec(query="topic:ai-tools")
        self.assertNotEqual(a.query_sha256, b.query_sha256)
        # Changing a parameter also changes the hash.
        c = GitHubSearchSpec(query="topic:ai-agent", per_page=25)
        self.assertNotEqual(a.query_sha256, c.query_sha256)


class UrlAndHeadersTest(unittest.TestCase):
    def test_url_uses_fixed_host_and_params(self):
        spec = GitHubSearchSpec(query="topic:example", sort="stars", order="asc", per_page=5)
        transport = _FakeTransport(response=_ok(_body([], 0)))
        _client(spec, transport).fetch(None)
        parsed = urlparse(transport.last_url)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.hostname, "api.github.com")
        self.assertIsNone(parsed.port)
        self.assertIsNone(parsed.username)
        qs = parse_qs(parsed.query)
        self.assertEqual(qs["q"], ["topic:example"])
        self.assertEqual(qs["sort"], ["stars"])
        self.assertEqual(qs["order"], ["asc"])
        self.assertEqual(qs["per_page"], ["5"])
        self.assertEqual(qs["page"], ["1"])

    def test_query_is_url_encoded(self):
        raw = "topic:ai agent pushed:>2026-08-01"
        spec = GitHubSearchSpec(query=raw)
        transport = _FakeTransport(response=_ok(_body([], 0)))
        _client(spec, transport).fetch(None)
        # The raw query (with spaces and colons) must not appear verbatim.
        self.assertNotIn(" ", transport.last_url)
        self.assertNotIn("topic:ai agent", transport.last_url)
        self.assertIn("%3A", transport.last_url)

    def test_headers_have_no_credentials(self):
        spec = GitHubSearchSpec(query="topic:example")
        transport = _FakeTransport(response=_ok(_body([], 0)))
        _client(spec, transport).fetch(None)
        self.assertEqual(
            set(transport.last_headers.keys()),
            {"Accept", "X-GitHub-Api-Version", "User-Agent"},
        )
        blob = json.dumps(transport.last_headers)
        for bad in ("token", "authorization", "cookie", "bearer"):
            self.assertNotIn(bad, blob.lower())

    def test_cursor_carries_no_query_or_url(self):
        spec = GitHubSearchSpec(query="topic:example", max_pages=3)
        transport = _FakeTransport(response=_ok(_body([_item(1)], 20)))
        client = _client(spec, transport)
        page = client.fetch(None)
        self.assertEqual(page.next_cursor, "page:2")
        self.assertNotIn("topic", page.next_cursor)
        self.assertNotIn("http", page.next_cursor)


class ResponseParsingTest(unittest.TestCase):
    def test_normal_items_from_fixture(self):
        body = (FIXTURES / "rest_search_page.json").read_bytes()
        spec = GitHubSearchSpec(query="topic:example")
        transport = _FakeTransport(response=_ok(body))
        page = _client(spec, transport).fetch(None)
        self.assertEqual(len(page.items), 2)
        self.assertEqual(page.next_cursor, "page:1")  # wrapped back to start

    def test_null_optional_fields_accepted(self):
        items = [{
            "id": 1, "full_name": "example-org/x",
            "html_url": "https://example.com/x", "description": None,
            "language": None, "pushed_at": None, "updated_at": "2026-08-02T00:00:00Z",
        }]
        spec = GitHubSearchSpec(query="x")
        transport = _FakeTransport(response=_ok(_body(items, 1)))
        page = _client(spec, transport).fetch(None)
        self.assertEqual(len(page.items), 1)

    def test_empty_items_list(self):
        spec = GitHubSearchSpec(query="x")
        transport = _FakeTransport(response=_ok(_body([], 0)))
        page = _client(spec, transport).fetch(None)
        self.assertEqual(page.items, ())
        self.assertEqual(page.next_cursor, "page:1")  # wrapped back to start

    def test_invalid_total_count_rejected(self):
        spec = GitHubSearchSpec(query="x")
        for bad_body in (
            b'{"total_count": -1, "items": []}',
            b'{"total_count": "x", "items": []}',
            b'{"items": []}',
        ):
            with self.subTest(body=bad_body):
                transport = _FakeTransport(response=_ok(bad_body))
                with self.assertRaises(GitHubClientError) as cm:
                    _client(spec, transport).fetch(None)
                self.assertEqual(cm.exception.code, "INVALID_RESPONSE")

    def test_items_not_a_list_rejected(self):
        spec = GitHubSearchSpec(query="x")
        transport = _FakeTransport(response=_ok(b'{"total_count": 0, "items": {}}'))
        with self.assertRaises(GitHubClientError) as cm:
            _client(spec, transport).fetch(None)
        self.assertEqual(cm.exception.code, "INVALID_RESPONSE")

    def test_non_json_body_rejected(self):
        spec = GitHubSearchSpec(query="x")
        transport = _FakeTransport(response=_ok(b"{not json"))
        with self.assertRaises(GitHubClientError) as cm:
            _client(spec, transport).fetch(None)
        self.assertEqual(cm.exception.code, "INVALID_JSON")

    def test_wrong_content_type_rejected(self):
        spec = GitHubSearchSpec(query="x")
        transport = _FakeTransport(
            response=HttpResponse(
                status=200, headers={"Content-Type": "text/html"},
                body=_body([], 0), final_url=_OK_FINAL,
            )
        )
        with self.assertRaises(GitHubClientError) as cm:
            _client(spec, transport).fetch(None)
        self.assertEqual(cm.exception.code, "INVALID_CONTENT_TYPE")

    def test_response_too_large_rejected(self):
        spec = GitHubSearchSpec(query="x")
        big = _body([_item(i) for i in range(5)], 5)
        transport = _FakeTransport(response=_ok(big))
        client = GitHubRestClient(spec, transport, max_response_bytes=10)
        with self.assertRaises(GitHubClientError) as cm:
            client.fetch(None)
        self.assertEqual(cm.exception.code, "RESPONSE_TOO_LARGE")

    def test_final_url_other_host_rejected(self):
        spec = GitHubSearchSpec(query="x")
        transport = _FakeTransport(
            response=_ok(_body([], 0), final_url="https://example.com/search/repositories")
        )
        with self.assertRaises(GitHubClientError) as cm:
            _client(spec, transport).fetch(None)
        self.assertEqual(cm.exception.code, "REDIRECT_BLOCKED")

    def test_https_downgrade_rejected(self):
        spec = GitHubSearchSpec(query="x")
        transport = _FakeTransport(
            response=_ok(_body([], 0), final_url="http://api.github.com/search/repositories")
        )
        with self.assertRaises(GitHubClientError) as cm:
            _client(spec, transport).fetch(None)
        self.assertEqual(cm.exception.code, "REDIRECT_BLOCKED")

    def test_final_url_credentials_and_port_rejected(self):
        spec = GitHubSearchSpec(query="x")
        for bad_url in (
            "https://u:p@api.github.com/search/repositories",
            "https://api.github.com:8443/search/repositories",
        ):
            with self.subTest(url=bad_url):
                transport = _FakeTransport(response=_ok(_body([], 0), final_url=bad_url))
                with self.assertRaises(GitHubClientError) as cm:
                    _client(spec, transport).fetch(None)
                self.assertEqual(cm.exception.code, "REDIRECT_BLOCKED")


class PaginationTest(unittest.TestCase):
    def test_none_starts_at_page_one(self):
        spec = GitHubSearchSpec(query="x", per_page=10, max_pages=3)
        transport = _FakeTransport(response=_ok(_body([_item(1)], 25)))
        _client(spec, transport).fetch(None)
        self.assertEqual(parse_qs(urlparse(transport.last_url).query)["page"], ["1"])

    def test_page2_cursor_requests_page_two(self):
        spec = GitHubSearchSpec(query="x", per_page=10, max_pages=3)
        transport = _FakeTransport(response=_ok(_body([_item(2)], 25)))
        _client(spec, transport).fetch("page:2")
        self.assertEqual(parse_qs(urlparse(transport.last_url).query)["page"], ["2"])

    def test_invalid_cursors_blocked(self):
        spec = GitHubSearchSpec(query="x", max_pages=3)
        client = _client(spec, _FakeTransport(response=_ok(_body([], 0))))
        bad_cursors = (
            "page:0", "page:-1", "page:x", "page:1 ", "page:4",
            "https://api.github.com/x", "garbage", " page:1", "page:",
        )
        for bad in bad_cursors:
            with self.subTest(cursor=bad):
                with self.assertRaises(GitHubClientError) as cm:
                    client.fetch(bad)
                self.assertEqual(cm.exception.code, "INVALID_CURSOR")

    def test_max_pages_reached_wraps_to_page_one(self):
        spec = GitHubSearchSpec(query="x", per_page=10, max_pages=1)
        transport = _FakeTransport(response=_ok(_body([_item(1)], 100)))
        page = _client(spec, transport).fetch(None)
        self.assertEqual(page.next_cursor, "page:1")

    def test_request_page_sequence_cycles_one_two_three_one(self):
        # Periodic scan: 1 -> 2 -> 3 -> wrap to 1 (not stuck on page 3).
        spec = GitHubSearchSpec(query="x", per_page=10, max_pages=3)
        transport = _FakeTransport(response=_ok(_body([_item(1)], 25)))
        client = _client(spec, transport)
        client.fetch(None)      # page 1
        client.fetch("page:2")  # page 2
        client.fetch("page:3")  # page 3 -> wraps to page:1
        client.fetch("page:1")  # page 1 again
        self.assertEqual(transport.requested_pages, ["1", "2", "3", "1"])
        self.assertEqual(transport.calls, 4)

    def test_max_pages_one_requests_page_one_every_call(self):
        spec = GitHubSearchSpec(query="x", per_page=10, max_pages=1)
        transport = _FakeTransport(response=_ok(_body([_item(1)], 100)))
        client = _client(spec, transport)
        first = client.fetch(None)
        second = client.fetch(first.next_cursor)
        # Each call is a real page-1 request (periodic re-scan, not a no-op).
        self.assertEqual(transport.calls, 2)
        self.assertEqual(transport.requested_pages, ["1", "1"])
        self.assertEqual(first.next_cursor, "page:1")
        self.assertEqual(second.next_cursor, "page:1")

    def test_cursor_format_is_only_page_n(self):
        spec = GitHubSearchSpec(query="x", per_page=10, max_pages=3)
        transport = _FakeTransport(response=_ok(_body([_item(1)], 25)))
        cursor = _client(spec, transport).fetch(None).next_cursor
        self.assertTrue(cursor.startswith("page:"))
        self.assertEqual(cursor, "page:2")


class ErrorMappingTest(unittest.TestCase):
    def _client_for(self, status, headers=None, body=b"{}", error=None):
        spec = GitHubSearchSpec(query="topic:marker-query")
        if error is not None:
            transport = _FakeTransport(error=error)
        else:
            transport = _FakeTransport(response=_status(status, headers, body))
        return spec, GitHubRestClient(spec, transport)

    def test_401_maps_to_auth(self):
        spec, client = self._client_for(401)
        with self.assertRaises(GitHubClientError) as cm:
            client.fetch(None)
        self.assertEqual(cm.exception.code, "HTTP_AUTH")

    def test_403_rate_limited_when_remaining_zero(self):
        spec, client = self._client_for(403, headers={"X-RateLimit-Remaining": "0"})
        with self.assertRaises(GitHubClientError) as cm:
            client.fetch(None)
        self.assertEqual(cm.exception.code, "RATE_LIMITED")

    def test_403_without_rate_limit_maps_to_auth(self):
        spec, client = self._client_for(403)
        with self.assertRaises(GitHubClientError) as cm:
            client.fetch(None)
        self.assertEqual(cm.exception.code, "HTTP_AUTH")

    def test_429_maps_to_rate_limited(self):
        spec, client = self._client_for(429)
        with self.assertRaises(GitHubClientError) as cm:
            client.fetch(None)
        self.assertEqual(cm.exception.code, "RATE_LIMITED")

    def test_500_maps_to_http_failure(self):
        spec, client = self._client_for(500)
        with self.assertRaises(GitHubClientError) as cm:
            client.fetch(None)
        self.assertEqual(cm.exception.code, "HTTP_FAILURE")

    def test_timeout_maps_to_network_timeout(self):
        spec, client = self._client_for(200, error=TimeoutError("timed out"))
        with self.assertRaises(GitHubClientError) as cm:
            client.fetch(None)
        self.assertEqual(cm.exception.code, "NETWORK_TIMEOUT")

    def test_transport_failure_maps_to_network_failure(self):
        spec, client = self._client_for(200, error=ConnectionError("refused"))
        with self.assertRaises(GitHubClientError) as cm:
            client.fetch(None)
        self.assertEqual(cm.exception.code, "NETWORK_FAILURE")

    def test_warnings_carry_no_sensitive_data(self):
        # Every failure must become a stable warning with no query/url/body.
        cases = [
            (lambda: self._client_for(429), "GITHUB_RATE_LIMITED"),
            (lambda: self._client_for(500), "GITHUB_HTTP_FAILURE"),
            (lambda: self._client_for(200, error=TimeoutError("timed out")), "GITHUB_NETWORK_TIMEOUT"),
            (lambda: self._client_for(200, error=ConnectionError("refused")), "GITHUB_NETWORK_FAILURE"),
        ]
        for make, expected_warning in cases:
            spec, client = make()
            batch = GitHubSource(client).collect(None, {})
            self.assertEqual(batch.status, "failed", expected_warning)
            self.assertEqual(batch.warnings, (expected_warning,))
            warning_blob = " ".join(batch.warnings)
            self.assertNotIn("topic:marker-query", warning_blob)
            self.assertNotIn("api.github.com", warning_blob)
            self.assertNotIn("refused", warning_blob)
            self.assertNotIn("timed out", warning_blob)


if __name__ == "__main__":
    unittest.main()
