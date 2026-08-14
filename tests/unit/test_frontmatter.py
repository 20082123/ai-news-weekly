"""Unit tests for :mod:`ai_signal.feedback.frontmatter`."""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.feedback.frontmatter import (  # noqa: E402
    FrontmatterError,
    parse_frontmatter,
    serialize_frontmatter,
)


class ParseFrontmatterTest(unittest.TestCase):
    def test_parse_valid(self):
        text = (
            "---\n"
            'target_type: "material_pack"\n'
            'target_id: "abc123"\n'
            "decision: null\n"
            "usefulness: 5\n"
            "---\n"
            "body text\n"
        )
        fm, body = parse_frontmatter(text)
        self.assertEqual(fm["target_type"], "material_pack")
        self.assertEqual(fm["target_id"], "abc123")
        self.assertIsNone(fm["decision"])
        self.assertEqual(fm["usefulness"], 5)
        self.assertIn("body text", body)

    def test_missing_delimiter_raises(self):
        with self.assertRaises(FrontmatterError):
            parse_frontmatter("no frontmatter here")

    def test_unknown_field_raises(self):
        with self.assertRaises(FrontmatterError):
            parse_frontmatter("---\nbogus_field: 1\n---\nbody\n")

    def test_control_char_raises(self):
        text = "---\nreason: \"hello\x00world\"\n---\nbody\n"
        with self.assertRaises(FrontmatterError):
            parse_frontmatter(text)

    def test_duplicate_key_rejected(self):
        text = "---\ndecision: \"parked\"\ndecision: \"adopted\"\n---\nbody\n"
        with self.assertRaises(FrontmatterError):
            parse_frontmatter(text)


class SerializeFrontmatterTest(unittest.TestCase):
    def test_round_trip(self):
        data = {
            "schema_version": 1,
            "target_type": "material_pack",
            "target_id": "xyz",
            "decision": None,
        }
        text = serialize_frontmatter(data)
        fm, _ = parse_frontmatter(text)
        self.assertEqual(fm["target_id"], "xyz")
        self.assertEqual(fm["schema_version"], 1)

    def test_string_with_quotes_and_backslash(self):
        # json.dumps / json.loads must round-trip quotes and backslashes exactly.
        tricky = 'he said "hi" and a\\backslash'
        text = serialize_frontmatter({"reason": tricky})
        fm, _ = parse_frontmatter(text)
        self.assertEqual(fm["reason"], tricky)

    def test_serialize_rejects_control_char(self):
        with self.assertRaises(FrontmatterError):
            serialize_frontmatter({"reason": "bad\nnewline"})


if __name__ == "__main__":
    unittest.main()
