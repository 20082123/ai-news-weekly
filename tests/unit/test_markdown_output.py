"""Unit tests for :mod:`ai_signal.outputs.markdown` (2B3 readable names)."""

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ai_signal.outputs.markdown import (  # noqa: E402
    MarkdownPublishError,
    build_markdown_filename,
    publish_pack_markdown,
)
from ai_signal.feedback.frontmatter import parse_frontmatter  # noqa: E402

_PACK_ID = "a" * 64
_PACK_ID_2 = "b" * 64
_EVENT_ID = "c" * 64
_CLAIM_ID = "1" * 64  # a realistic 64-hex claim id (hidden from the body)
_FACT = "仓库 example-org/example-repo 可通过 https://example.com/repo 公开访问。"
_WEEK = "2026-W33"
_CONTENT = {
    "schema_version": "material-pack-v2",
    "signal_card": {"full_name": "example-org/example-repo"},
    "project_info": {
        "full_name": "example-org/example-repo",
        "description": "an example repository",
        "topics": ["ai", "agents"],
        "language": "Python",
        "stargazers_count": 42,
        "forks_count": 7,
        "updated_at": "2026-08-11T00:00:00+00:00",
        "pushed_at": "2026-08-10T00:00:00+00:00",
        "html_url": "https://example.com/example-org/example-repo",
    },
    "A_what_happened": {
        "claim_ids": [_CLAIM_ID],
        "facts": [_FACT],
    },
    "B_evidence_limits_unknowns": {
        "evidence_id": "e1",
        "source": "github",
        "url": "https://example.com/repo",
        "snippet": "example-repo - updated 2026-08-11",
        "unknowns": ["仅基于单次快照"],
        "limitations": ["Point-in-time"],
    },
    "C_why_now_heat": {
        "heat_status": "unmeasured",
        "heat_status_label": "尚未测量",
        "score": 0.5,
        "notes": [
            "热度趋势尚未测量：缺少历史快照，无法判断是否升温。",
            "stars/forks 只是当前快照数值，不是增长速度。",
            "后续积累历史快照后才能比较热度变化。",
        ],
    },
    "D_audience_impacts": {"editorial_hypothesis": True, "angles": [{"who_label": "知识工作者", "note": "x"}]},
    "E_manual_test_plan": {"steps": ["阅读 README"]},
    "F_channel_adaptations": {"editorial_outline": True, "bilibili_outline": "outline"},
}
_LINKS = {_CLAIM_ID: "https://example.com/repo"}


def _publish(root, pack_id=_PACK_ID, event_id=_EVENT_ID, week=_WEEK, content=None):
    return publish_pack_markdown(
        root, pack_id, event_id, week, content or _CONTENT, _LINKS
    )


class BuildMarkdownFilenameTest(unittest.TestCase):
    def test_hax_exact_name(self):
        name, needs_suffix = build_markdown_filename(
            "2026-W33", "OleksandrChekhovskyi/hax", "e" * 64
        )
        self.assertEqual(name, "2026-W33 - GitHub - hax (OleksandrChekhovskyi).md")
        self.assertFalse(needs_suffix)

    def test_name_has_no_64_hex_pack_hash(self):
        name, _ = build_markdown_filename("2026-W33", "example-org/example-repo", "f" * 64)
        self.assertNotIn("f" * 64, name)
        self.assertNotIn("a" * 64, name)

    def test_illegal_chars_sanitized(self):
        name, _ = build_markdown_filename(
            "2026-W33", 'bad<>:"/\\|?*owner/bad<>repo', "e" * 64
        )
        for ch in '<>:"/\\|?*':
            self.assertNotIn(ch, name)
        self.assertTrue(name.endswith(".md"))

    def test_control_chars_and_trailing_dots_removed(self):
        name, _ = build_markdown_filename(
            "2026-W33", "owner/repo\x00.. ", "e" * 64
        )
        self.assertNotIn("\x00", name)
        stem = name[:-3]
        self.assertFalse(stem.endswith("."))
        self.assertFalse(stem.endswith(" "))

    def test_overlong_name_truncated_with_suffix(self):
        long_repo = "r" * 300
        name, needs_suffix = build_markdown_filename("2026-W33", "owner/" + long_repo, "e" * 64)
        self.assertTrue(needs_suffix)
        self.assertLessEqual(len(name), 120)
        self.assertIn("e" * 8, name)  # short event_id suffix

    def test_reserved_device_name_gets_suffix(self):
        name, needs_suffix = build_markdown_filename("2026-W33", "owner/CON", "e" * 64)
        self.assertTrue(needs_suffix)


class MarkdownPublishTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ai_md_")
        self.root = pathlib.Path(self.tmp)
        self.inbox = self.root / "Inbox"

    def _path(self):
        files = list(self.inbox.glob("*.md"))
        return files[0]

    def test_write_new_file_with_readable_name(self):
        created = _publish(self.root)
        self.assertTrue(created)
        path = self.inbox / "2026-W33 - GitHub - example-repo (example-org).md"
        self.assertTrue(path.exists())

    def test_chinese_sections_and_project_info(self):
        _publish(self.root)
        text = self._path().read_text(encoding="utf-8")
        for heading in (
            "## 项目信息",
            "## A — 发生了什么",
            "## B — 证据、限定与未知",
            "## C — 为什么现在值得关注",
            "## D — 对不同受众的影响（编辑假设）",
            "## E — 本周亲测方案",
            "## F — B站 / 小红书 / 抖音改编思路",
            "## 事实声明与来源",
            "## 人工反馈填写说明",
        ):
            self.assertIn(heading, text)
        # Project info from the collected payload is visible.
        self.assertIn("an example repository", text)
        self.assertIn("ai、agents", text)
        self.assertIn("42", text)
        self.assertIn("https://example.com/example-org/example-repo", text)

    def test_claims_show_chinese_fact_text_with_evidence_link(self):
        _publish(self.root)
        _, body = parse_frontmatter(self._path().read_text(encoding="utf-8"))
        self.assertIn(
            "- %s — [来源](https://example.com/repo)" % _FACT, body
        )

    def test_visible_body_has_no_64hex_claim_id(self):
        _publish(self.root)
        text = self._path().read_text(encoding="utf-8")
        fm, body = parse_frontmatter(text)
        # The claim id stays in the structured pack (frontmatter carries only
        # pack/event ids) and never appears in the visible body.
        self.assertNotIn(_CLAIM_ID, body)
        self.assertEqual(fm["target_id"], _PACK_ID)

    def test_c_notes_all_rendered(self):
        _publish(self.root)
        _, body = parse_frontmatter(self._path().read_text(encoding="utf-8"))
        for note in _CONTENT["C_why_now_heat"]["notes"]:
            self.assertIn("- 说明：%s" % note, body)

    def test_claim_facts_length_mismatch_refuses(self):
        content = dict(_CONTENT)
        content["A_what_happened"] = {
            "claim_ids": [_CLAIM_ID, "2" * 64],
            "facts": [_FACT],  # one fact missing -> refuse, never mismatch
        }
        with self.assertRaises(MarkdownPublishError):
            _publish(self.root, content=content)

    def test_frontmatter_keeps_machine_fields_and_ids(self):
        _publish(self.root)
        fm, _ = parse_frontmatter(self._path().read_text(encoding="utf-8"))
        self.assertEqual(fm["target_type"], "material_pack")
        self.assertEqual(fm["target_id"], _PACK_ID)
        self.assertEqual(fm["event_id"], _EVENT_ID)
        self.assertEqual(fm["week_key"], _WEEK)
        self.assertIsNone(fm["decision"])

    def test_rerun_same_event_week_updates_same_single_file(self):
        _publish(self.root)
        _publish(self.root)  # same pack id
        files = list(self.inbox.glob("*.md"))
        self.assertEqual(len(files), 1)

    def test_new_pack_id_updates_target_id_and_keeps_feedback(self):
        _publish(self.root, pack_id=_PACK_ID)
        # Human edits the feedback fields.
        path = self._path()
        text = path.read_text(encoding="utf-8")
        text = text.replace("decision: null", 'decision: "adopted"')
        text = text.replace("usefulness: null", "usefulness: 4")
        path.write_text(text, encoding="utf-8")

        created = _publish(self.root, pack_id=_PACK_ID_2)
        self.assertFalse(created)
        files = list(self.inbox.glob("*.md"))
        self.assertEqual(len(files), 1)  # no duplicate file
        fm, _ = parse_frontmatter(files[0].read_text(encoding="utf-8"))
        self.assertEqual(fm["target_id"], _PACK_ID_2)  # refreshed
        self.assertEqual(fm["decision"], "adopted")    # preserved
        self.assertEqual(fm["usefulness"], 4)          # preserved

    def test_event_mismatch_never_overwrites_existing_file(self):
        _publish(self.root)
        original = self._path()
        before = original.read_text(encoding="utf-8")
        # A different event with the same readable name: the original file is
        # untouched (a suffixed file is created for the new event instead).
        _publish(self.root, pack_id=_PACK_ID_2, event_id="d" * 64)
        self.assertEqual(original.read_text(encoding="utf-8"), before)
        fm, _ = parse_frontmatter(before)
        self.assertEqual(fm["target_id"], _PACK_ID)  # original unchanged

    def test_week_mismatch_writes_separate_file_not_overwrite(self):
        _publish(self.root)
        # A different week is a different document: a second file appears and
        # the first is untouched.
        _publish(self.root, week="2026-W34")
        files = sorted(self.inbox.glob("*.md"))
        self.assertEqual(len(files), 2)

    def test_invalid_preserved_decision_refused(self):
        _publish(self.root)
        path = self._path()
        text = path.read_text(encoding="utf-8").replace(
            "decision: null", 'decision: "bogus"'
        )
        path.write_text(text, encoding="utf-8")
        with self.assertRaises(MarkdownPublishError):
            _publish(self.root, pack_id=_PACK_ID_2)

    def test_invalid_existing_frontmatter_refused(self):
        self.inbox.mkdir(parents=True)
        target = self.inbox / "2026-W33 - GitHub - example-repo (example-org).md"
        target.write_text("garbage without frontmatter", encoding="utf-8")
        with self.assertRaises(MarkdownPublishError):
            _publish(self.root)

    def test_html_in_body_escaped(self):
        content = dict(_CONTENT)
        content["signal_card"] = {"full_name": "org/repo-x"}
        content["project_info"] = {
            "full_name": "<script>alert(1)</script>",
            "description": "<img src=x onerror=alert(1)>",
        }
        _publish(self.root, content=content)
        text = self._path().read_text(encoding="utf-8")
        self.assertNotIn("<script>", text)
        self.assertNotIn("<img", text)

    def test_collision_with_different_event_uses_suffix(self):
        _publish(self.root, event_id=_EVENT_ID)
        # Same readable name, different event: a suffixed file is created and
        # the original is untouched.
        other = dict(_CONTENT)
        _publish(self.root, pack_id=_PACK_ID_2, event_id="e" * 64, content=other)
        files = sorted(self.inbox.glob("*.md"))
        self.assertEqual(len(files), 2)
        self.assertTrue(any(f.name.endswith("[%s].md" % ("e" * 8)) for f in files))

    def test_path_traversal_via_title_rejected(self):
        # The title feeds the filename; traversal payloads are sanitized, and
        # the resolved target stays inside Inbox.
        content = dict(_CONTENT)
        content["signal_card"] = {"full_name": "../../evil/repo"}
        _publish(self.root, content=content)
        files = list(self.inbox.glob("*.md"))
        self.assertEqual(len(files), 1)
        resolved = files[0].resolve()
        self.assertEqual(resolved.parent, (self.root / "Inbox").resolve())

    def test_symlinked_inbox_rejected(self):
        inbox = self.root / "Inbox"
        inbox.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        try:
            inbox.rmdir()
            inbox.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink not supported")
        with self.assertRaises(MarkdownPublishError):
            _publish(self.root)


if __name__ == "__main__":
    unittest.main()
