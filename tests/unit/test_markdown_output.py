"""Unit tests for :mod:`ai_signal.outputs.markdown` (atomic write, frontmatter)."""

import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.outputs.markdown import (  # noqa: E402
    MarkdownPublishError,
    publish_pack_markdown,
)
from ai_signal.feedback.frontmatter import parse_frontmatter  # noqa: E402

_PACK_ID = "a" * 64
_EVENT_ID = "b" * 64
_CONTENT = {
    "schema_version": "material-pack-v1",
    "signal_card": {"full_name": "example-org/example-repo"},
    "A_what_happened": {
        "claim_ids": ["c1"],
        "facts": ["Repository example-repo is publicly accessible at https://example.com/repo."],
    },
    "B_evidence_limits_unknowns": {
        "evidence_id": "e1",
        "source": "github",
        "url": "https://example.com/repo",
        "snippet": "example-repo - updated 2026-08-11",
        "unknowns": ["Single snapshot"],
        "limitations": ["Point-in-time"],
    },
    "C_why_now_heat": {"heat_status": "unmeasured", "score": 0.5, "score_components": {}},
    "D_audience_impacts": {"editorial_hypothesis": True, "angles": [{"who": "devs", "note": "x"}]},
    "E_manual_test_plan": {"steps": ["Read README"]},
    "F_channel_adaptations": {"editorial_outline": True, "bilibili_outline": "outline"},
}
_LINKS = {"c1": "https://example.com/repo"}


class MarkdownPublishTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai_md_")
        self.root = pathlib.Path(self.tmp)

    def _path(self):
        return self.root / "Inbox" / (_PACK_ID + ".md")

    def test_write_new_file(self):
        created = publish_pack_markdown(
            self.root, _PACK_ID, _EVENT_ID, "2026-W33", _CONTENT, _LINKS
        )
        self.assertTrue(created)
        path = self._path()
        self.assertTrue(path.exists())
        text = path.read_text(encoding="utf-8")
        fm, body = parse_frontmatter(text)
        self.assertEqual(fm["target_id"], _PACK_ID)
        self.assertIn("[evidence](https://example.com/repo)", body)

    def test_rerun_preserves_valid_human_frontmatter(self):
        publish_pack_markdown(self.root, _PACK_ID, _EVENT_ID, "2026-W33", _CONTENT, _LINKS)
        path = self._path()
        text = path.read_text(encoding="utf-8")
        text = text.replace("decision: null", 'decision: "adopted"')
        text = text.replace("usefulness: null", "usefulness: 5")
        path.write_text(text, encoding="utf-8")
        created = publish_pack_markdown(
            self.root, _PACK_ID, _EVENT_ID, "2026-W33", _CONTENT, _LINKS
        )
        self.assertFalse(created)
        fm, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        self.assertEqual(fm["decision"], "adopted")
        self.assertEqual(fm["usefulness"], 5)

    def test_invalid_preserved_decision_refused(self):
        publish_pack_markdown(self.root, _PACK_ID, _EVENT_ID, "2026-W33", _CONTENT, _LINKS)
        path = self._path()
        text = path.read_text(encoding="utf-8").replace(
            "decision: null", 'decision: "bogus"'
        )
        path.write_text(text, encoding="utf-8")
        with self.assertRaises(MarkdownPublishError):
            publish_pack_markdown(
                self.root, _PACK_ID, _EVENT_ID, "2026-W33", _CONTENT, _LINKS
            )

    def test_invalid_preserved_published_url_refused(self):
        publish_pack_markdown(self.root, _PACK_ID, _EVENT_ID, "2026-W33", _CONTENT, _LINKS)
        path = self._path()
        text = path.read_text(encoding="utf-8").replace(
            "published_url: null", 'published_url: "http://example.com"'
        )
        path.write_text(text, encoding="utf-8")
        with self.assertRaises(MarkdownPublishError):
            publish_pack_markdown(
                self.root, _PACK_ID, _EVENT_ID, "2026-W33", _CONTENT, _LINKS
            )

    def test_mismatched_target_id_refused(self):
        publish_pack_markdown(self.root, _PACK_ID, _EVENT_ID, "2026-W33", _CONTENT, _LINKS)
        path = self._path()
        text = path.read_text(encoding="utf-8").replace(
            _PACK_ID, "c" * 64
        )
        path.write_text(text, encoding="utf-8")
        with self.assertRaises(MarkdownPublishError):
            publish_pack_markdown(
                self.root, _PACK_ID, _EVENT_ID, "2026-W33", _CONTENT, _LINKS
            )

    def test_invalid_existing_frontmatter_refused(self):
        path = self._path()
        path.parent.mkdir(parents=True)
        path.write_text("garbage without frontmatter", encoding="utf-8")
        with self.assertRaises(MarkdownPublishError):
            publish_pack_markdown(
                self.root, _PACK_ID, _EVENT_ID, "2026-W33", _CONTENT, _LINKS
            )

    def test_non_hex_pack_id_rejected(self):
        with self.assertRaises(MarkdownPublishError):
            publish_pack_markdown(
                self.root, "not-hex", _EVENT_ID, "2026-W33", _CONTENT, _LINKS
            )

    def test_path_traversal_pack_id_rejected(self):
        # A pack id carrying ".." or path separators must never escape Inbox.
        for bad in ("../" + "a" * 62, "a" * 32 + "..", "a" * 31 + "/" + "b" * 32):
            with self.subTest(pack_id=bad):
                with self.assertRaises(MarkdownPublishError):
                    publish_pack_markdown(
                        self.root, bad, _EVENT_ID, "2026-W33", _CONTENT, _LINKS
                    )
        # Nothing was written.
        self.assertFalse((self.root / "Inbox").exists())

    def test_html_in_body_escaped(self):
        content = dict(_CONTENT)
        content["signal_card"] = {"full_name": "<script>alert(1)</script>"}
        publish_pack_markdown(self.root, _PACK_ID, _EVENT_ID, "2026-W33", content, _LINKS)
        text = self._path().read_text(encoding="utf-8")
        self.assertNotIn("<script>", text)

    def test_symlinked_inbox_rejected(self):
        inbox = self.root / "Inbox"
        inbox.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        try:
            (self.root / "Inbox_link").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink not supported")
        # A symlinked "Inbox" pointing outside must be rejected.
        inbox_link = self.root / "Inbox"
        inbox_link.rmdir()
        inbox_link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(MarkdownPublishError):
            publish_pack_markdown(
                self.root, _PACK_ID, _EVENT_ID, "2026-W33", _CONTENT, _LINKS
            )


if __name__ == "__main__":
    unittest.main()
