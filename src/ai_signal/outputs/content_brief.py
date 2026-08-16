"""Phase 2F content brief Markdown output (deterministic, quote-only).

Renders one dossier + facts + editorial decision into a Chinese content
brief file: 选题卡 (what happened / who cares / what to do), 事实与来源
(quoted facts with first-party URLs), 限制与禁说清单, 本人测试计划 and B站
母内容/平台复用角度. Every sentence derives from stored facts - no LLM, no
fabricated conclusions, no experience claims unless the decision allows it.

Writes only under ``<output-root>/Content/`` with the same filesystem safety
rules as the other outputs (no symlink / ``..`` escapes, temp + atomic
replace, one-file failure never corrupts the database).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from ..domain.models import (
    EditorialDecision,
    EventCandidate,
    ResearchDossier,
    ResearchFact,
    content_brief_entity_id,
)


class ContentBriefError(Exception):
    """Raised when a brief cannot be published safely."""


_INVALID_FILENAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _safe_stem(text: str, cap: int = 72) -> str:
    stem = _INVALID_FILENAME_RE.sub("-", text or "brief").strip()
    stem = stem.replace("..", "--")  # never leave parent-directory glyphs
    stem = re.sub(r"\s+", " ", stem)
    stem = stem.rstrip(". ")
    if not stem:
        stem = "brief"
    return stem[:cap]


def _render_brief(
    week_key: str,
    event: EventCandidate,
    dossier: ResearchDossier,
    facts,
    decision: EditorialDecision,
) -> str:
    lines = []
    lines.append("---")
    lines.append("kind: content-brief")
    lines.append("week_key: %s" % week_key)
    lines.append("event_candidate_id: %s" % event.id)
    lines.append("dossier_id: %s" % dossier.id)
    lines.append("brief_id: %s" % content_brief_entity_id(week_key, event.id))
    lines.append("signal_type: %s" % event.signal_type)
    lines.append("editorial: %s" % decision.decision)
    lines.append("# 第一轮反馈（读卡后判断选题，可填可不填）")
    lines.append("decision: null        # adopted / parked / rejected")
    lines.append("reason: null          # 为什么，<=500字")
    lines.append("audience: null        # 你判断的真实受众")
    lines.append("angle: null           # 你实际用的角度")
    lines.append("usefulness: null      # 1-5")
    lines.append("# 第二轮反馈（发布几天后，必填）")
    lines.append("published_url: null   # 发布后的 https 链接")
    lines.append("published_at: null    # 发布日期 YYYY-MM-DD")
    lines.append("outcome: null         # 表现如何：阅读/点赞/收藏/评论，捡你在乎的写")
    lines.append("lesson: null          # 一句话复盘：下次改什么")
    lines.append("---")
    lines.append("")
    lines.append("# 选题卡：%s" % event.subject)
    lines.append("")
    lines.append("## 一句话判断（机器草案）")
    lines.append("")
    lines.append(dossier.summary_judgment)
    lines.append("")
    lines.append("## Editorial（%s）" % decision.decision)
    lines.append("")
    lines.append("理由：%s" % "、".join(decision.reason_codes))
    lines.append("")

    # --- 角度（四问翻译草稿）：把零件翻成"具体的人、具体的活儿" ----------
    # 机器只能填它从事实里能推出的部分；②③的"具体到人+前后数字"是核心手艺，
    # 必须人工完成——这正是从零件到初稿的那一步。
    has_prev_release = any(
        f.kind == "fact" and f.text.startswith("上一版本") for f in facts
    )
    if decision.decision == "ready_to_write":
        action_draft = "现在可以报道发生了什么；「去用 / 等等看 / 不用管」三选一（待人工定）。"
        angle_draft = "报道向：把①讲清楚 + 给读者一个行动建议，可直接改写成初稿。"
    elif decision.decision == "needs_testing":
        action_draft = "先按下方测试计划亲测，再写体验结论；在此之前只能写「官方宣称」。"
        angle_draft = "亲测向：「我替你把 %s 试了一遍」（成与翻车都是素材，需先完成测试计划）。" % event.subject
    else:
        action_draft = "不用管 / 观察：证据不足，等下一轮快照再看。"
        angle_draft = "踩刹车向：「%s 看起来新鲜，但我替你查过了，先别急着用」。" % event.subject

    lines.append("## 角度（四问翻译草稿）")
    lines.append("")
    lines.append("- ① 变化是什么（无术语版）：%s" % dossier.summary_judgment)
    lines.append("- ② 谁的任务变了：%s（待人工补到「人群+任务」级别）"
                % dossier.target_audience)
    if has_prev_release:
        lines.append("- ③ 之前 vs 现在：有上一版本对比（见时间线）；")
        lines.append("  具体的「省多少时间/钱/步骤」数字待人工补——这是最值钱的一句。")
    else:
        lines.append("- ③ 之前 vs 现在：待人工补（前后对比数字，最值钱的一句）。")
    lines.append("- ④ 现在该做什么：%s" % action_draft)
    lines.append("")
    lines.append("机器建议角度：%s" % angle_draft)
    lines.append("")
    lines.append("标题草稿（人工）：____")
    lines.append("")

    lines.append("## 发生了什么（时间线）")
    lines.append("")
    if dossier.timeline:
        for item in dossier.timeline:
            lines.append("- %s" % item)
    else:
        lines.append("- （无明确时间线）")
    lines.append("")
    lines.append("## 事实与来源")
    lines.append("")
    for fact in facts:
        source = "（%s）" % fact.source_url if fact.source_url else ""
        lines.append("- [%s] %s %s" % (fact.kind, fact.text, source))
    lines.append("")
    lines.append("## 目标受众与任务")
    lines.append("")
    lines.append("- 受众：%s" % dossier.target_audience)
    lines.append("- 任务：%s" % dossier.job_to_be_done)
    lines.append("")
    lines.append("## 限制、反例和未知")
    lines.append("")
    for item in dossier.limits_unknowns:
        lines.append("- %s" % item)
    lines.append("")
    lines.append("## 禁说清单")
    lines.append("")
    for item in dossier.forbidden_claims:
        lines.append("- %s" % item)
    lines.append("")
    if dossier.needs_testing and dossier.test_plan:
        lines.append("## 本人测试计划")
        lines.append("")
        for item in dossier.test_plan:
            lines.append("- %s" % item)
        lines.append("")
    lines.append("## B站母内容角度（建议）")
    lines.append("")
    lines.append("- 开场：%s 具体发生了什么（时间线第一项）。" % event.subject)
    lines.append("- 主体：逐条引用「事实与来源」，每条给出来源链接。")
    lines.append("- 结尾：属于谁、影响哪个任务、现在该做什么（按 Editorial 结论）。")
    lines.append("- 钩子：优先使用可核数字（价格、日期、版本号），不用未核实的趋势词。")
    lines.append("")
    lines.append("## 平台复用提示")
    lines.append("")
    lines.append("- 小红书：开头一句结论 + 3 个数字 + 「来源见官方链接」。")
    lines.append("- 抖音：时间线第一项做口播开场，禁说清单做结尾免责。")
    lines.append("- 全部平台：体验型结论只有本人测试完成后才能加入。")
    lines.append("")
    lines.append("## 反馈（人填，两轮）")
    lines.append("")
    lines.append("- 第一轮（读卡后，可填可不填）：frontmatter 里 decision 选 "
                "adopted/parked/rejected，可加 reason/audience/angle/usefulness。")
    lines.append("- 第二轮（发布几天后，必填）：published_url + published_at(YYYY-MM-DD) "
                "+ outcome（真实表现）+ lesson（一句话复盘）。")
    lines.append("- 填完跑：feedback sync --content-dir <本目录>（由代理执行），反馈落库。")
    lines.append("")
    return "\n".join(lines)


def publish_content_brief(
    output_root: Path,
    week_key: str,
    event: EventCandidate,
    dossier: ResearchDossier,
    facts,
    decision: EditorialDecision,
) -> Path:
    """Write one brief file atomically under ``<root>/Content/``.

    Raises :class:`ContentBriefError` on any filesystem safety problem; the
    caller decides how to surface it (never a database error).
    """
    content = _render_brief(week_key, event, dossier, facts, decision)
    target_dir = output_root / "Content"
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ContentBriefError("unable to create Content directory") from exc
    filename = "%s - %s.md" % (week_key, _safe_stem(event.subject))
    target = target_dir / filename
    try:
        # Resolve and re-check: no symlink / .. escapes outside the root.
        resolved_root = output_root.resolve()
        resolved_target = target.resolve()
        if not str(resolved_target).startswith(str(resolved_root)):
            raise ContentBriefError("output path escapes the output root")
    except OSError as exc:
        raise ContentBriefError("unable to validate output path") from exc
    tmp = target.with_name(".%s.tmp" % target.name)
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)
    except OSError as exc:
        raise ContentBriefError("unable to write brief file") from exc
    return target
