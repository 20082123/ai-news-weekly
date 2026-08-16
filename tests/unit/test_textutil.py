"""Unit tests for shared text cleaning (textutil + research sanitize)."""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.pipeline.research import _clean_external_text  # noqa: E402
from ai_signal.textutil import (  # noqa: E402
    strip_html_tags,
    strip_markdown_links,
)


class StripHtmlTagsTest(unittest.TestCase):
    def test_strips_tags_and_unescapes(self):
        out = strip_html_tags(
            '<div align="center"><img src="x">badge</div> 正文 &amp; 更多'
        )
        self.assertEqual(out, "badge 正文 & 更多")

    def test_script_and_style_blocks_removed(self):
        out = strip_html_tags("a<script>alert(1)</script>b<style>.x{}</style>c")
        self.assertEqual(out, "a b c")

    def test_dangling_half_tag_dropped(self):
        # A tag truncated mid-way (no closing >) must not survive.
        out = strip_html_tags('前面 <a href="https://qwenlm.github.io/qwen-c')
        self.assertEqual(out, "前面")

    def test_collapses_whitespace(self):
        out = strip_html_tags("  a\n\n   b\t c ")
        self.assertEqual(out, "a b c")

    def test_empty_and_none(self):
        self.assertEqual(strip_html_tags(""), "")
        self.assertEqual(strip_html_tags("<b></b>"), "")


class StripMarkdownLinksTest(unittest.TestCase):
    def test_link_becomes_anchor_text(self):
        out = strip_markdown_links("看 [文档](https://docs.example.com/x) 吧")
        self.assertEqual(out, "看 文档 吧")

    def test_badge_image_inside_link_dropped(self):
        out = strip_markdown_links(
            "[![npm version](https://img.shields.io/npm/v/x.svg)]"
            "(https://www.npmjs.com/package/x)"
        )
        self.assertEqual(out, "")

    def test_bare_urls_kept(self):
        out = strip_markdown_links("Changelog: https://github.com/a/b")
        self.assertEqual(out, "Changelog: https://github.com/a/b")


class CleanExternalTextTest(unittest.TestCase):
    def test_injection_flag_fires_before_stripping(self):
        text, flagged = _clean_external_text(
            "x <script>alert(1)</script> y", 500
        )
        self.assertTrue(flagged)
        self.assertEqual(text, "（内容含可疑注入标记，已脱敏）")

    def test_html_stripped_for_storage(self):
        text, flagged = _clean_external_text(
            '<div align="center">badge</div> 正文', 500
        )
        self.assertFalse(flagged)
        self.assertNotIn("<div", text)
        self.assertIn("badge 正文", text)

    def test_markdown_links_cleaned_before_cap(self):
        text, flagged = _clean_external_text(
            "[![npm version](https://img.shields.io/npm/v/x.svg)]"
            "(https://www.npmjs.com/package/x) **介绍** <a href=", 500
        )
        self.assertFalse(flagged)
        self.assertNotIn("img.shields.io", text)
        self.assertNotIn("<a href", text)
        self.assertIn("介绍", text)

    def test_cap_applied_after_cleaning(self):
        text, _ = _clean_external_text("<b>" + "字" * 100 + "</b>", 20)
        self.assertLessEqual(len(text), 20)


if __name__ == "__main__":
    unittest.main()
