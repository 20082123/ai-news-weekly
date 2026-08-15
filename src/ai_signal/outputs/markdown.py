"""Safe, atomic Markdown publisher for the phase 2B2/2B3 Inbox.

Writes one file per ``(event, week)`` under ``output_root/Inbox`` using a
human-readable, tentative filename::

    <week_key> - GitHub - <repo> (<owner>).md

The filename policy is encapsulated in :func:`build_markdown_filename` so it
can be swapped later. ``pack_id`` / ``event_id`` live in the frontmatter and
SQLite, never in the normal filename; a short ``event_id`` suffix is only
appended on truncation or a name collision.

Safety rules:

* the resolved target must remain inside ``output_root/Inbox`` (no symlink,
  junction, ``..`` or file-link escape);
* writing uses a same-directory temp file + ``os.replace`` (atomic);
* Windows-illegal characters, control characters, trailing spaces/dots and
  reserved device names are sanitized; a length cap is enforced;
* frontmatter is strict YAML-compatible JSON-scalar; the six human feedback
  fields are preserved across rebuilds - including when a new ``pack_id`` is
  produced for the same event/week, in which case ``target_id`` is updated to
  the new pack id after validating the old one;
* files whose frontmatter ``event_id``/``week_key`` do not match are never
  overwritten (a collision falls back to a suffixed filename);
* untrusted text is HTML-escaped before reaching the Markdown body.

The Markdown body is Chinese (phase 2B3); machine frontmatter field names and
the ``decision`` enum stay English so feedback sync remains compatible.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple
from urllib.parse import urlparse

from ..feedback.frontmatter import (
    FrontmatterError,
    parse_frontmatter,
    serialize_frontmatter,
)

_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_FEEDBACK_DECISIONS = ("adopted", "parked", "rejected")

# Filename policy constants (tentative; swap via build_markdown_filename).
_FILENAME_MAX = 100  # budget for the name portion, excluding ".md"
_ILLEGAL_CHARS = '<>:"/\\|?*'
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class MarkdownPublishError(Exception):
    """Raised when a Markdown file cannot be safely written."""


# --------------------------------------------------------------------------- #
# Filename policy
# --------------------------------------------------------------------------- #
def _sanitize_component(text: str) -> str:
    """Make one filename component Windows-safe."""
    cleaned = "".join(
        " " if (ch in _ILLEGAL_CHARS or ord(ch) < 0x20 or ord(ch) == 0x7F) else ch
        for ch in text
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def build_markdown_filename(
    week_key: str,
    full_name: str,
    event_id: str,
) -> Tuple[str, bool]:
    """Build the tentative readable filename for one event.

    Returns ``(filename, needs_suffix)``. ``needs_suffix`` is True when the
    name was truncated (or would be ambiguous), in which case the caller
    appends a short ``event_id`` suffix to keep the name unique.
    """
    owner = ""
    repo = full_name
    if "/" in full_name:
        owner, _, repo = full_name.partition("/")
        owner = owner.strip()
        repo = repo.strip()
    repo = repo or "repository"
    owner = owner or "unknown-owner"

    base = "%s - GitHub - %s (%s)" % (
        _sanitize_component(week_key),
        _sanitize_component(repo),
        _sanitize_component(owner),
    )
    base = base.strip().rstrip(". ")

    needs_suffix = False
    if len(base) > _FILENAME_MAX:
        base = base[:_FILENAME_MAX].strip().rstrip(". ")
        needs_suffix = True
    if repo.upper().split(".")[0] in _RESERVED_NAMES:
        needs_suffix = True
    if needs_suffix and event_id:
        base = "%s (%s)" % (base, event_id[:8])
    if not base:
        base = event_id[:8] or "unnamed"
    return base + ".md", needs_suffix


def _escape(text: Any) -> str:
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _safe_https_url(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlparse(value)
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and parsed.username is None
        and parsed.password is None
        and parsed.port is None
    )


# --------------------------------------------------------------------------- #
# Chinese body (phase 2B3)
# --------------------------------------------------------------------------- #
_FEEDBACK_GUIDE = """## 人工反馈填写说明

请在顶部 frontmatter 中填写以下字段（字段名与取值保持英文，便于程序同步）：

- `decision`: 人工决策，只能填 `adopted`（采纳）、`parked`（搁置）或 `rejected`（拒绝）；留空 `null` 表示尚未决策。
- `usefulness`: 素材有用程度，1–5 的整数，1 = 最低，5 = 最高；可留空 `null`。
- `reason`: 决策原因的自由文本说明。
- `audience`: 建议的目标受众。
- `angle`: 建议的切入角度。
- `published_url`: 成稿发布后的 HTTPS 链接。
"""


def _fmt(value: Any, placeholder: str = "（暂无）") -> str:
    if value is None or value == "" or value == []:
        return placeholder
    return _escape(str(value))


def _build_body(pack_content: Mapping[str, Any], claim_evidence_links: Mapping[str, str]) -> str:
    """Build the Chinese machine-generated Markdown body (no frontmatter)."""
    lines: list[str] = []
    sc = pack_content.get("signal_card", {})
    lines.append("# %s" % _escape(sc.get("full_name", "repository")))
    lines.append("")

    # --- 项目信息 (from the collected snapshot payload only) ---------------
    info = pack_content.get("project_info", {})
    lines.append("## 项目信息")
    lines.append("")
    lines.append("- 仓库名称：%s" % _fmt(info.get("full_name")))
    lines.append("- 项目简介：%s" % _fmt(info.get("description")))
    lines.append("- topics：%s" % _fmt("、".join(info.get("topics") or []) if info.get("topics") else None))
    lines.append("- 主要语言：%s" % _fmt(info.get("language")))
    lines.append("- stars：%s" % _fmt(info.get("stargazers_count")))
    lines.append("- forks：%s" % _fmt(info.get("forks_count")))
    lines.append("- 最近更新时间：%s" % _fmt(info.get("updated_at")))
    lines.append("- 最近推送时间：%s" % _fmt(info.get("pushed_at")))
    source_url = info.get("html_url") or sc.get("url") or ""
    if source_url:
        lines.append("- GitHub 来源链接：[%s](%s)" % (_escape(source_url), _escape(source_url)))
    lines.append("")

    a = pack_content.get("A_what_happened", {})
    lines.append("## A — 发生了什么")
    lines.append("")
    for fact in a.get("facts", []):
        lines.append("- %s" % _escape(fact))
    lines.append("")

    b = pack_content.get("B_evidence_limits_unknowns", {})
    lines.append("## B — 证据、限定与未知")
    lines.append("")
    ev_url = b.get("url", "")
    if ev_url:
        lines.append("- [Evidence](%s)" % _escape(ev_url))
    for unk in b.get("unknowns", []):
        lines.append("- 未知：%s" % _escape(unk))
    for lim in b.get("limitations", []):
        lines.append("- 限定：%s" % _escape(lim))
    lines.append("")

    # 事实声明与来源: show the human-readable Chinese fact text, not the
    # 64-hex claim id (ids stay in the structured pack and SQLite).
    claim_ids = list(a.get("claim_ids", []))
    facts = list(a.get("facts", []))
    if len(claim_ids) != len(facts):
        # Malformed pack content: refuse rather than risk mismatching a fact
        # with another fact's evidence link.
        raise MarkdownPublishError("claim_ids and facts length mismatch")
    lines.append("## 事实声明与来源")
    lines.append("")
    for cid, fact in zip(claim_ids, facts):
        link = claim_evidence_links.get(cid, "")
        if link:
            lines.append("- %s — [来源](%s)" % (_escape(fact), _escape(link)))
        else:
            lines.append("- %s" % _escape(fact))
    lines.append("")

    c = pack_content.get("C_why_now_heat", {})
    lines.append("## C — 为什么现在值得关注")
    lines.append("")
    lines.append("- 热度状态：%s" % _escape(c.get("heat_status_label", "尚未测量")))
    lines.append("- 确定性评分：%s" % _escape(c.get("score", "")))
    for note in c.get("notes", []):
        lines.append("- 说明：%s" % _escape(note))
    lines.append("")

    d = pack_content.get("D_audience_impacts", {})
    lines.append("## D — 对不同受众的影响（编辑假设）")
    lines.append("")
    for angle in d.get("angles", []):
        lines.append("- %s：%s" % (_escape(angle.get("who_label", "")), _escape(angle.get("note", ""))))
    lines.append("")

    e = pack_content.get("E_manual_test_plan", {})
    lines.append("## E — 本周亲测方案")
    lines.append("")
    for step in e.get("steps", []):
        lines.append("- %s" % _escape(step))
    lines.append("")

    f = pack_content.get("F_channel_adaptations", {})
    lines.append("## F — B站 / 小红书 / 抖音改编思路")
    lines.append("")
    lines.append("- B站：%s" % _escape(f.get("bilibili_outline", "")))
    lines.append("- 小红书：%s" % _escape("；".join(f.get("xiaohongshu_points", []))))
    lines.append("- 抖音：%s" % _escape(f.get("douyin_hook", "")))
    lines.append("")

    lines.append(_FEEDBACK_GUIDE)
    return "\n".join(lines)


def _machine_frontmatter(pack_id: str, event_id: str, week_key: str) -> str:
    return serialize_frontmatter(
        {
            "schema_version": 1,
            "target_type": "material_pack",
            "target_id": pack_id,
            "event_id": event_id,
            "week_key": week_key,
            "decision": None,
            "reason": None,
            "audience": None,
            "angle": None,
            "usefulness": None,
            "published_url": None,
        }
    )


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _prepare_inbox(output_root: Path) -> Path:
    """Validate ``output_root`` and return the resolved Inbox directory."""
    try:
        root = output_root.resolve(strict=False)
    except OSError as exc:
        raise MarkdownPublishError("cannot resolve output root") from exc

    if output_root.is_symlink():
        raise MarkdownPublishError("output root is a symlink")

    inbox = root / "Inbox"
    if inbox.exists() and inbox.is_symlink():
        raise MarkdownPublishError("inbox is a symlink")

    inbox.mkdir(parents=True, exist_ok=True)
    inbox_resolved = inbox.resolve(strict=True)
    if not _is_within(inbox_resolved, root):
        raise MarkdownPublishError("inbox escaped output root")
    return inbox_resolved


def _read_frontmatter(path: Path) -> Optional[Mapping[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
        fm, _ = parse_frontmatter(text)
        return fm
    except (FrontmatterError, OSError, UnicodeDecodeError):
        return None


def _find_existing_for_event(inbox: Path, event_id: str, week_key: str) -> Optional[Path]:
    """Find the (single) existing Inbox file for this event/week, if any."""
    for path in sorted(inbox.glob("*.md")):
        if path.is_symlink():
            continue
        fm = _read_frontmatter(path)
        if fm is None:
            continue
        if fm.get("event_id") == event_id and fm.get("week_key") == week_key:
            return path
    return None


def _validate_preserved(fm: Mapping[str, Any], event_id: str, week_key: str) -> None:
    """Validate an existing file's frontmatter before an in-place rebuild.

    The old ``target_id`` must be a valid 64-hex pack id (so a corrupted or
    foreign file is never overwritten), and ``event_id``/``week_key`` must
    match the current build. Feedback values must still be valid.
    """
    if fm.get("target_type") != "material_pack":
        raise MarkdownPublishError("frontmatter target_type mismatch")
    old_target = fm.get("target_id")
    if not isinstance(old_target, str) or not _HEX_RE.match(old_target):
        raise MarkdownPublishError("frontmatter target_id invalid")
    if fm.get("event_id") != event_id:
        raise MarkdownPublishError("frontmatter event_id mismatch")
    if fm.get("week_key") != week_key:
        raise MarkdownPublishError("frontmatter week_key mismatch")

    decision = fm.get("decision")
    if decision is not None and decision not in _FEEDBACK_DECISIONS:
        raise MarkdownPublishError("frontmatter decision invalid")

    usefulness = fm.get("usefulness")
    if usefulness is not None:
        if isinstance(usefulness, bool) or not isinstance(usefulness, int):
            raise MarkdownPublishError("frontmatter usefulness invalid")
        if not 1 <= usefulness <= 5:
            raise MarkdownPublishError("frontmatter usefulness invalid")

    published_url = fm.get("published_url")
    if published_url is not None and not _safe_https_url(published_url):
        raise MarkdownPublishError("frontmatter published_url invalid")


def _select_new_target(inbox: Path, week_key: str, title: str, event_id: str) -> Path:
    """Pick the filename for a brand-new file, avoiding collisions."""
    filename, needs_suffix = build_markdown_filename(week_key, title, event_id)
    candidate = inbox / filename
    if not candidate.exists():
        _ensure_inside(inbox, candidate)
        return candidate
    fm = _read_frontmatter(candidate)
    if fm is None:
        # The computed name is occupied by a file we cannot inspect; refuse
        # rather than risk destroying unknown (possibly human) content.
        raise MarkdownPublishError("existing frontmatter invalid; not overwriting")
    if fm.get("event_id") == event_id and fm.get("week_key") == week_key:
        _ensure_inside(inbox, candidate)
        return candidate
    # Genuine collision with a different event -> fall back to a suffixed name.
    base, ext = filename[:-3], filename[-3:]
    suffixed = inbox / ("%s [%s]%s" % (base, event_id[:8], ext))
    if suffixed.exists():
        fm2 = _read_frontmatter(suffixed)
        if fm2 is None or fm2.get("event_id") != event_id or fm2.get("week_key") != week_key:
            raise MarkdownPublishError("filename collision cannot be resolved")
    _ensure_inside(inbox, suffixed)
    return suffixed


def _ensure_inside(inbox: Path, target: Path) -> None:
    resolved = target.resolve(strict=False)
    if resolved.parent != inbox:
        raise MarkdownPublishError("target escaped inbox")


def publish_pack_markdown(
    output_root: Path,
    pack_id: str,
    event_id: str,
    week_key: str,
    content: Mapping[str, Any],
    claim_evidence_links: Mapping[str, str],
) -> bool:
    """Write (or rebuild) the Chinese Markdown file for one event/week.

    Returns ``True`` if a new file was created, ``False`` if an existing file
    was rebuilt. When the same event/week produces a new ``pack_id``, the
    existing readable file is updated in place: human feedback fields are
    preserved and ``target_id`` is refreshed to the new pack id.
    """
    if not _HEX_RE.match(pack_id):
        raise MarkdownPublishError("pack id must be 64 hex chars")

    inbox = _prepare_inbox(output_root)
    body = _build_body(content, claim_evidence_links)
    title = str(content.get("signal_card", {}).get("full_name", ""))

    # Rebuild path: reuse the existing readable file for this event/week.
    existing = _find_existing_for_event(inbox, event_id, week_key)
    if existing is not None:
        fm = _read_frontmatter(existing)
        if fm is None:
            raise MarkdownPublishError("existing frontmatter invalid; not overwriting")
        _validate_preserved(fm, event_id, week_key)
        preserved = {
            "schema_version": 1,
            "target_type": "material_pack",
            "target_id": pack_id,  # refreshed to the new pack id
            "event_id": event_id,
            "week_key": week_key,
            "decision": fm.get("decision"),
            "reason": fm.get("reason"),
            "audience": fm.get("audience"),
            "angle": fm.get("angle"),
            "usefulness": fm.get("usefulness"),
            "published_url": fm.get("published_url"),
        }
        machine_fm = serialize_frontmatter(preserved)
        _atomic_write(existing, machine_fm + "\n" + body + "\n")
        return False

    target = _select_new_target(inbox, week_key, title, event_id)
    machine_fm = _machine_frontmatter(pack_id, event_id, week_key)
    _atomic_write(target, machine_fm + "\n" + body + "\n")
    return True


def _atomic_write(target: Path, text: str) -> None:
    """Write ``text`` to ``target`` via a same-directory temp + ``os.replace``."""
    directory = target.parent
    fd, tmp_name = None, None
    try:
        import tempfile

        fd, tmp_name = tempfile.mkstemp(prefix=".tmp_", suffix=".md", dir=str(directory))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp_name, target)
    except Exception:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        raise
