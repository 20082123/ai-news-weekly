"""Unit tests for :mod:`ai_signal.observability.logging`."""

import copy
import json
import pathlib
import sys
import unittest
from io import StringIO

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.observability import logging as L  # noqa: E402


class RedactionTest(unittest.TestCase):
    def test_top_level_sensitive_key(self):
        out = L.redact({"authorization": "Bearer abc", "keep": "me"})
        self.assertEqual(out["authorization"], "[REDACTED]")
        self.assertEqual(out["keep"], "me")

    def test_nested_sensitive_key(self):
        out = L.redact({"a": {"api_key": "secret", "ok": 1}})
        self.assertEqual(out["a"]["api_key"], "[REDACTED]")
        self.assertEqual(out["a"]["ok"], 1)

    def test_deeply_nested_sensitive_key(self):
        out = L.redact({"l1": {"l2": {"l3": {"access_token": "x"}}}})
        self.assertEqual(out["l1"]["l2"]["l3"]["access_token"], "[REDACTED]")

    def test_sensitive_key_case_insensitive(self):
        out = L.redact({"API_Key": "x", "Cookie": "y"})
        self.assertEqual(out["API_Key"], "[REDACTED]")
        self.assertEqual(out["Cookie"], "[REDACTED]")

    def test_email_redaction(self):
        out = L.redact({"contact": "ping user@example.com anytime"})
        self.assertNotIn("user@example.com", out["contact"])
        self.assertIn("[EMAIL]", out["contact"])

    def test_url_query_redaction(self):
        out = L.redact({"url": "https://example.com/path?api_key=secret&keep=this"})
        self.assertNotIn("secret", out["url"])
        self.assertIn("keep=this", out["url"])

    def test_url_without_sensitive_params_unchanged(self):
        url = "https://example.com/path?keep=this"
        out = L.redact({"url": url})
        self.assertEqual(out["url"], url)

    def test_does_not_mutate_input(self):
        original = {"api_key": "secret", "nested": {"token": "t"}, "list": [{"cookie": "c"}]}
        snapshot = copy.deepcopy(original)
        L.redact(original)
        self.assertEqual(original, snapshot)

    def test_long_text_truncated(self):
        out = L.redact({"blob": "x" * 5000})
        self.assertLess(len(out["blob"]), 5000)
        self.assertIn("[TRUNCATED]", out["blob"])

    def test_unknown_object_stringified_safely(self):
        class Weird:
            def __str__(self):
                return "weird-string"

        out = L.redact({"obj": Weird()})
        self.assertIsInstance(out["obj"], str)
        self.assertEqual(out["obj"], "weird-string")

    def test_scalars_preserved(self):
        out = L.redact({"a": 1, "b": 1.5, "c": True, "d": None})
        self.assertEqual(out["a"], 1)
        self.assertEqual(out["b"], 1.5)
        self.assertIs(out["c"], True)
        self.assertIsNone(out["d"])


class EmitTest(unittest.TestCase):
    def test_emit_single_line_json(self):
        buf = StringIO()
        L.emit({"level": "INFO", "event": "started", "secret": "hush"}, stream=buf)
        line = buf.getvalue().strip()
        self.assertEqual(line.count("\n"), 0)
        parsed = json.loads(line)
        self.assertEqual(parsed["level"], "INFO")
        self.assertEqual(parsed["secret"], "[REDACTED]")

    def test_structured_logger_emits_canonical_fields(self):
        buf = StringIO()
        logger = L.StructuredLogger(run_id="run-1", stage="collect", source="news", stream=buf)
        logger.info("page-fetched", status="ok", duration_ms=12)
        parsed = json.loads(buf.getvalue().strip())
        for field in (
            "timestamp",
            "level",
            "run_id",
            "stage",
            "source",
            "event",
            "status",
            "duration_ms",
            "error_code",
        ):
            self.assertIn(field, parsed)
        self.assertEqual(parsed["run_id"], "run-1")
        self.assertEqual(parsed["duration_ms"], 12)


if __name__ == "__main__":
    unittest.main()
