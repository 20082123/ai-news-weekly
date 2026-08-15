"""Optional debug Markdown output for qualified candidates (phase 2C1).

This publisher is **optional debug output**, not a material Inbox: by default
qualification writes only SQLite. When the CLI is explicitly given
``--emit-candidate-markdown --output-root <path> --allow-output-write``, only
RESEARCH candidates render a Chinese debug card under
``<output_root>/Candidates/Research/``. WATCH and REJECT stay database-only,
and ``Candidates/Watch/`` is never created. The legacy Inbox/Weekly/Published
directories and :mod:`ai_signal.outputs.markdown` are untouched.

The card is explicitly a research/debug queue, never publishable material: no
A-F content, no publishability wording (READY_TO_WRITE / NEEDS_TESTING /
建议发布 / 可以直接写), no unsupported trend claims, and no 64-hex internal id
in the visible body (ids live in the frontmatter and SQLite).

Safety rules: Windows-illegal character / control-char / trailing-dot
sanitization, reserved device names, length cap with a short candidate-id
suffix fallback on truncation/collision, symlink and ``..`` escape rejection,
same-directory temp + ``os.replace`` atomic writes, in-place rebuild per
candidate, and no overwriting of files belonging to other candidates.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple

from ..domain.models import Candidate, CandidateAssessment, CandidateDiscovery
from ..pipeline.qualify import QualifiedCandidate

_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_FILENAME_MAX = 100
_ILLEGAL_CHARS = '<>:"/\\|?*'
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

_FM_FIELDS = (
    "type",
    "candidate_id",
    "discovery_id",
    "assessment_id",
    "week_key",
    "source",
    "scope_key",
    "lane",
    "qualification_decision",
    "qualification_policy",
)


class CandidateMarkdownError(Exception):
    """Raised when a candidate card cannot be safely written."""


# --------------------------------------------------------------------------- #
# Filename policy
# --------------------------------------------------------------------------- #
def _sanitize_component(text: str) -> str:
    cleaned = "".join(
        " " if (ch in _ILLEGAL_CHARS or ord(ch) < 0x20 or ord(ch) == 0x7F) else ch
        for ch in text
    )
    return re.sub(r"\s+", " ", cleaned).strip()


def build_candidate_filename(
    week_key: str, full_name: str, candidate_id: str
) -> Tuple[str, bool]:
    """Build the tentative readable debug-card filename.

    Returns ``(filename, needs_suffix)``; the short candidate-id suffix is
    only appended on truncation / reserved names (collision handling happens
    at publish time).
    """
    owner = ""
    repo = full_name
    if "/" in full_name:
        owner, _, repo = full_name.partition("/")
        owner, repo = owner.strip(), repo.strip()
    repo = repo or "repository"
    owner = owner or "unknown-owner"

    base = "%s - 候选 - %s (%s)" % (
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
    if needs_suffix and candidate_id:
        base = "%s (%s)" % (base, candidate_id[:8])
    if not base:
        base = candidate_id[:8] or "unnamed"
    return base + ".md", needs_suffix


# --------------------------------------------------------------------------- #
# Frontmatter (dedicated strict scalar subset; machine fields stay English)
# --------------------------------------------------------------------------- #
def _format_fm_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
            raise CandidateMarkdownError("control character in frontmatter value")
        return json.dumps(value, ensure_ascii=False)
    raise CandidateMarkdownError("unsupported frontmatter scalar")


def _serialize_frontmatter(data: Mapping[str, Any]) -> str:
    lines = ["---"]
    for key in _FM_FIELDS:
        if key in data:
            lines.append("%s: %s" % (key, _format_fm_scalar(data[key])))
    lines.append("---")
    return "\n".join(lines)


def _parse_frontmatter(text: str) -> Optional[Mapping[str, Any]]:
    """Strictly parse our own frontmatter subset; ``None`` when invalid."""
    if not text.startswith("---"):
        return None
    lines = text.split("\n")
    if lines[0].strip() != "---":
        return None
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return None
    result: dict = {}
    for raw in lines[1:end]:
        line = raw.rstrip()
        if not line.strip():
            continue
        m = re.match(r"^([a-z_]+)\s*:\s*(.*)$", line)
        if not m:
            return None
        key, value = m.group(1), m.group(2).strip()
        if key not in _FM_FIELDS or key in result:
            return None
        if value == "null":
            result[key] = None
        elif value.startswith('"') and value.endswith('"') and len(value) >= 2:
            try:
                inner = json.loads(value)
            except ValueError:
                return None
            if not isinstance(inner, str):
                return None
            result[key] = inner
        elif re.fullmatch(r"-?\d+", value):
            result[key] = int(value)
        else:
            return None
    return result


# --------------------------------------------------------------------------- #
# Chinese debug-card body
# --------------------------------------------------------------------------- #
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


def _fmt(value: Any) -> str:
    if value is None or value == "" or value == []:
        return "（暂无）"
    return _escape(str(value))


def _build_body(qc: QualifiedCandidate) -> str:
    cand = qc.candidate
    disc = qc.discovery
    assess = qc.assessment
    attrs = assess.attributes
    lines: list[str] = []

    lines.append("# %s" % _escape(cand.title))
    lines.append("")
    lines.append("> **这是候选卡（调试/研究队列），不是可发布素材。**")
    lines.append("")

    # 1. 候选状态
    lines.append("## 1. 候选状态")
    lines.append("")
    lines.append("- 判定：%s" % _escape("RESEARCH（值得继续调研）"))
    lines.append("- 政策版本：%s" % _escape(assess.policy_version))
    lines.append("")

    # 2. 发现来源与 Lane
    lines.append("## 2. 发现来源与 Lane")
    lines.append("")
    lines.append("- 来源：%s" % _escape(cand.source))
    lines.append("- Lane：%s（发现来源标签与 Gate 策略，尚非完整 Discovery Policy）" % _escape(disc.lane))
    lines.append("- Week：%s" % _escape(disc.week_key))
    lines.append("")

    # 3. 当前触发
    lines.append("## 3. 当前触发")
    lines.append("")
    lines.append("- 触发类型：%s" % _escape(assess.trigger_kind))
    lines.append("- 触发摘要：%s" % _escape(assess.trigger_summary))
    lines.append("")

    # 4. 为什么可能值得继续研究
    lines.append("## 4. 为什么可能值得继续研究")
    lines.append("")
    lines.append("- 判定理由代码：%s" % _escape("、".join(assess.reason_codes) or "（无）"))
    lines.append(
        "- 说明：仅表示值得进入 GitHub Researcher 继续阅读 README、Release 等资料，"
        "不代表值得发布，也不代表发生了重要事件。"
    )
    lines.append("")

    # 5. 当前已有的仓库元数据（homepage 只显示存在性，不渲染 URL）
    lines.append("## 5. 当前已有的仓库元数据")
    lines.append("")
    lines.append("- 仓库名称：%s" % _fmt(attrs.get("full_name")))
    lines.append("- 项目简介：%s" % _fmt(attrs.get("description")))
    lines.append("- topics：%s" % _fmt("、".join(attrs.get("topics") or []) if attrs.get("topics") else None))
    lines.append("- 主要语言：%s" % _fmt(attrs.get("language")))
    lines.append("- stars：%s" % _fmt(attrs.get("stargazers_count")))
    lines.append("- forks：%s" % _fmt(attrs.get("forks_count")))
    lines.append("- 创建时间：%s" % _fmt(attrs.get("created_at")))
    lines.append("- 最近更新时间：%s" % _fmt(attrs.get("updated_at")))
    lines.append("- 最近推送时间：%s" % _fmt(attrs.get("pushed_at")))
    lines.append(
        "- 主页：%s" % ("已填写（未核验）" if attrs.get("homepage_present") else "（暂无）")
    )
    lines.append("")

    # 6. 缺失证据
    lines.append("## 6. 缺失证据")
    lines.append("")
    lines.append("当前判定仅基于 GitHub 搜索快照元数据，以下证据全部缺失：")
    lines.append("")
    for item in assess.missing_evidence:
        lines.append("- %s" % _escape(item))
    lines.append("")

    # 7. 下一步建议
    lines.append("## 7. 下一步建议")
    lines.append("")
    lines.append("- 由 GitHub Researcher 阅读 README、Release 与相关文档，确认是否存在可报道的具体事件。")
    lines.append("- 在确认 specific_event 之前，不进入任何编辑或发布流程。")
    lines.append("")

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Publishing
# --------------------------------------------------------------------------- #
def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _prepare_research_dir(output_root: Path) -> Path:
    try:
        root = output_root.resolve(strict=False)
    except OSError as exc:
        raise CandidateMarkdownError("cannot resolve output root") from exc
    if output_root.is_symlink():
        raise CandidateMarkdownError("output root is a symlink")

    target_dir = root / "Candidates" / "Research"
    if target_dir.exists() and target_dir.is_symlink():
        raise CandidateMarkdownError("candidates dir is a symlink")
    target_dir.mkdir(parents=True, exist_ok=True)
    resolved = target_dir.resolve(strict=True)
    if not _is_within(resolved, root):
        raise CandidateMarkdownError("candidates dir escaped output root")
    return resolved


def _read_frontmatter(path: Path) -> Optional[Mapping[str, Any]]:
    try:
        return _parse_frontmatter(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None


def _find_existing(directory: Path, candidate_id: str, week_key: str) -> Optional[Path]:
    for path in sorted(directory.glob("*.md")):
        if path.is_symlink():
            continue
        fm = _read_frontmatter(path)
        if fm and fm.get("candidate_id") == candidate_id and fm.get("week_key") == week_key:
            return path
    return None


def _ensure_inside(directory: Path, target: Path) -> None:
    if target.resolve(strict=False).parent != directory:
        raise CandidateMarkdownError("target escaped candidates dir")


def _select_new_target(
    directory: Path, week_key: str, title: str, candidate_id: str
) -> Path:
    filename, _ = build_candidate_filename(week_key, title, candidate_id)
    candidate = directory / filename
    if not candidate.exists():
        _ensure_inside(directory, candidate)
        return candidate
    fm = _read_frontmatter(candidate)
    if fm is None:
        raise CandidateMarkdownError("existing frontmatter invalid; not overwriting")
    if fm.get("candidate_id") == candidate_id and fm.get("week_key") == week_key:
        _ensure_inside(directory, candidate)
        return candidate
    base, ext = filename[:-3], filename[-3:]
    suffixed = directory / ("%s [%s]%s" % (base, candidate_id[:8], ext))
    if suffixed.exists():
        fm2 = _read_frontmatter(suffixed)
        if fm2 is None or fm2.get("candidate_id") != candidate_id:
            raise CandidateMarkdownError("filename collision cannot be resolved")
    _ensure_inside(directory, suffixed)
    return suffixed


def _machine_frontmatter(qc: QualifiedCandidate) -> str:
    return _serialize_frontmatter(
        {
            "type": "ai-signal-candidate",
            "candidate_id": qc.candidate.id,
            "discovery_id": qc.discovery.id,
            "assessment_id": qc.assessment.id,
            "week_key": qc.discovery.week_key,
            "source": qc.candidate.source,
            "scope_key": qc.discovery.scope_key,
            "lane": qc.discovery.lane,
            "qualification_decision": qc.assessment.decision,
            "qualification_policy": qc.assessment.policy_version,
        }
    )


def publish_candidate_markdown(
    output_root: Path, qc: QualifiedCandidate
) -> bool:
    """Write (or rebuild) the debug card for one RESEARCH candidate.

    Returns ``True`` for a new file, ``False`` for an in-place rebuild. A
    rebuild refreshes ``assessment_id`` while the candidate identity (and
    filename) stay stable.

    Any filesystem failure (``OSError`` / ``PermissionError`` from mkdir,
    ``os.replace``, temp-file creation or write) is converted to a
    :class:`CandidateMarkdownError` carrying no filesystem path or OS error
    text, so callers never leak a local path.
    """
    if qc.assessment.decision != "research":
        raise CandidateMarkdownError("only research candidates render markdown")
    if not _HEX_RE.match(qc.candidate.id) or not _HEX_RE.match(qc.assessment.id):
        raise CandidateMarkdownError("ids must be 64 hex chars")

    try:
        directory = _prepare_research_dir(output_root)
        body = _build_body(qc)
        fm_text = _machine_frontmatter(qc)

        existing = _find_existing(directory, qc.candidate.id, qc.discovery.week_key)
        if existing is not None:
            _atomic_write(existing, fm_text + "\n" + body + "\n")
            return False

        target = _select_new_target(
            directory, qc.discovery.week_key, qc.candidate.title, qc.candidate.id
        )
        _atomic_write(target, fm_text + "\n" + body + "\n")
        return True
    except OSError as exc:
        raise CandidateMarkdownError("cannot write candidate markdown") from exc


def _atomic_write(target: Path, text: str) -> None:
    import tempfile

    directory = target.parent
    fd, tmp_name = None, None
    try:
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
