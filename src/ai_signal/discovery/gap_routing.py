"""Evidence-gap routing: which source answers which hole (DEC-017).

The sources never queue behind each other. A creator choice has an evidence
gap list; each gap names ONE best source. The agent (Codex/DSH/WorkBuddy)
executes the routing - the system never scrapes Reddit/X on its own.
"""

from __future__ import annotations

from typing import Tuple

# gap key -> (中文名, 首选来源, 怎么补)
GAP_ROUTING = {
    "official_confirmation": (
        "官方确认",
        "official",
        # DEC-018: official sites are a dictionary, not a radar - query the
        # exact page on demand instead of scanning feeds for discoveries.
        "官网按需查证（research add-evidence --url 官方页）；official collect 仅被动备份",
    ),
    "technical_implementation": (
        "技术实现与版本",
        "github",
        "GitHub 仓库/Release（research build）",
    ),
    "user_reality": (
        "用户现实",
        "reddit",
        "Reddit 真实使用/失败案例（agent-reach 采集 → research add-note）",
    ),
    "early_signal": (
        "早期信号",
        "x",
        "X 首发/作者讨论（agent-reach 采集 → research add-note）",
    ),
    "personal_testing": (
        "本人亲测",
        "human",
        "只有第一用户能补（needs_testing 时按测试计划执行）",
    ),
}

ALL_GAPS: Tuple[str, ...] = tuple(GAP_ROUTING)


def classify_fact_source(source_kind: str, source_url: str) -> str:
    """Map one stored fact to the gap it covers (or '' when unclassifiable)."""
    if source_kind in ("github_release", "github_readme", "github_metadata"):
        return "technical_implementation"
    if source_kind == "official_page":
        return "official_confirmation"
    if source_kind == "manual":
        lowered = (source_url or "").lower()
        if "reddit.com" in lowered:
            return "user_reality"
        if "x.com" in lowered or "twitter.com" in lowered:
            return "early_signal"
        return "user_reality"  # manual quotes default to user reality
    return ""


def assess_evidence_gaps(fact_source_kinds):
    """Return the gap keys not yet covered by the given fact source kinds.

    ``fact_source_kinds`` is an iterable of ``(source_kind, source_url)``.
    Gaps are reported in routing order; ``personal_testing`` only appears
    when the dossier flagged ``needs_testing``.
    """
    covered = {
        classify_fact_source(kind, url)
        for kind, url in fact_source_kinds
        if classify_fact_source(kind, url)
    }
    gaps = [gap for gap in ALL_GAPS if gap != "personal_testing" and gap not in covered]
    if "personal_testing" not in covered:
        gaps.append("personal_testing")
    return tuple(gaps)
