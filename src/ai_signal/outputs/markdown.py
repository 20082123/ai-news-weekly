"""Safe, atomic Markdown publisher for the phase 2B2 Inbox.

Writes ``Inbox/<material_pack_id>.md`` under a caller-supplied
``output_root``. Safety rules:

* the resolved target must remain inside ``output_root/Inbox`` (no symlink,
  junction, ``..`` or file-link escape);
* writing uses a same-directory temp file + ``os.replace`` (atomic);
* filenames come only from stable hex ids;
* frontmatter is strict YAML-compatible JSON-scalar; the six human feedback
  fields are preserved across rebuilds, but only if they are still valid and
  still point at this exact pack/event/week - otherwise the file is not
  overwritten (so human feedback is never lost);
* untrusted text is HTML-escaped before reaching the Markdown body.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from ..feedback.frontmatter import (
    FrontmatterError,
    parse_frontmatter,
    serialize_frontmatter,
)

_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_FEEDBACK_DECISIONS = ("adopted", "parked", "rejected")


class MarkdownPublishError(Exception):
    """Raised when a Markdown file cannot be safely written."""


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


def _build_body(pack_content: Mapping[str, Any], claim_evidence_links: Mapping[str, str]) -> str:
    """Build the machine-generated Markdown body (no frontmatter)."""
    lines: list[str] = []
    sc = pack_content.get("signal_card", {})
    lines.append("# %s" % _escape(sc.get("full_name", "repository")))
    lines.append("")

    a = pack_content.get("A_what_happened", {})
    lines.append("## A — What happened")
    lines.append("")
    for fact in a.get("facts", []):
        lines.append("- %s" % _escape(fact))
    lines.append("")

    b = pack_content.get("B_evidence_limits_unknowns", {})
    lines.append("## B — Evidence, limits, unknowns")
    lines.append("")
    ev_url = b.get("url", "")
    if ev_url:
        lines.append("- [Evidence](%s)" % _escape(ev_url))
    for unk in b.get("unknowns", []):
        lines.append("- Unknown: %s" % _escape(unk))
    for lim in b.get("limitations", []):
        lines.append("- Limitation: %s" % _escape(lim))
    lines.append("")

    lines.append("## Claims")
    lines.append("")
    for cid in a.get("claim_ids", []):
        link = claim_evidence_links.get(cid, "")
        if link:
            lines.append("- %s — [evidence](%s)" % (_escape(cid), _escape(link)))
        else:
            lines.append("- %s" % _escape(cid))
    lines.append("")

    c = pack_content.get("C_why_now_heat", {})
    lines.append("## C — Why now / heat")
    lines.append("")
    lines.append("- heat_status: %s" % _escape(c.get("heat_status", "unmeasured")))
    lines.append("- score: %s" % _escape(c.get("score", "")))
    lines.append("")

    d = pack_content.get("D_audience_impacts", {})
    lines.append("## D — Audience impacts (editorial hypothesis)")
    lines.append("")
    for angle in d.get("angles", []):
        lines.append("- %s: %s" % (_escape(angle.get("who", "")), _escape(angle.get("note", ""))))
    lines.append("")

    e = pack_content.get("E_manual_test_plan", {})
    lines.append("## E — Manual test plan")
    lines.append("")
    for step in e.get("steps", []):
        lines.append("- %s" % _escape(step))
    lines.append("")

    f = pack_content.get("F_channel_adaptations", {})
    lines.append("## F — Channel adaptations (editorial outline)")
    lines.append("")
    lines.append("- Bilibili: %s" % _escape(f.get("bilibili_outline", "")))
    lines.append("- Douyin: %s" % _escape(f.get("douyin_hook", "")))
    lines.append("")

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


def _resolve_inbox_path(output_root: Path, pack_id: str) -> Path:
    """Resolve ``Inbox/<pack_id>.md`` and prove it stays inside ``output_root``."""
    if not _HEX_RE.match(pack_id):
        raise MarkdownPublishError("pack id must be 64 hex chars")

    # Canonical absolute root, resolving any .. in the caller-supplied path.
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

    target = (inbox_resolved / (pack_id + ".md")).resolve(strict=False)
    if not _is_within(target, inbox_resolved):
        raise MarkdownPublishError("target escaped inbox")
    return target


def _validate_preserved(fm: Mapping[str, Any], pack_id: str, event_id: str, week_key: str) -> None:
    """Reject overwrite if preserved frontmatter no longer matches / is invalid."""
    if fm.get("target_type") != "material_pack":
        raise MarkdownPublishError("frontmatter target_type mismatch")
    if fm.get("target_id") != pack_id:
        raise MarkdownPublishError("frontmatter target_id mismatch")
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


def publish_pack_markdown(
    output_root: Path,
    pack_id: str,
    event_id: str,
    week_key: str,
    content: Mapping[str, Any],
    claim_evidence_links: Mapping[str, str],
) -> bool:
    """Write (or rebuild) the Markdown file for a pack.

    Returns ``True`` if a new file was created, ``False`` if an existing file
    was rebuilt (valid human frontmatter preserved). Raises
    :class:`MarkdownPublishError` on any safety violation.
    """
    target = _resolve_inbox_path(output_root, pack_id)
    body = _build_body(content, claim_evidence_links)

    if target.exists():
        if target.is_symlink():
            raise MarkdownPublishError("refusing symlink")
        existing = target.read_text(encoding="utf-8")
        try:
            fm, _ = parse_frontmatter(existing)
        except FrontmatterError:
            raise MarkdownPublishError("existing frontmatter invalid; not overwriting")
        _validate_preserved(fm, pack_id, event_id, week_key)
        # Preserve the human-edited feedback fields verbatim.
        preserved = {
            "schema_version": 1,
            "target_type": "material_pack",
            "target_id": pack_id,
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
        _atomic_write(target, machine_fm + "\n" + body + "\n")
        return False

    machine_fm = _machine_frontmatter(pack_id, event_id, week_key)
    _atomic_write(target, machine_fm + "\n" + body + "\n")
    return True


def _atomic_write(target: Path, text: str) -> None:
    """Write ``text`` to ``target`` via a same-directory temp + ``os.replace``."""
    directory = target.parent
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp_", suffix=".md", dir=str(directory))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
