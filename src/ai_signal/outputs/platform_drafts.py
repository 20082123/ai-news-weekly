"""Draft outputs for publishing (DEC-021): prompt pack + platform skeleton.

Both are DETERMINISTIC assemblies of stored facts: the prompt pack is the
guardrail bundle for external writing models (Doubao etc.), the platform
skeleton is the per-platform typesetting shell with expression slots left
blank. The system never writes prose - it only places verified parts.

Files are written under ``<output-root>/Drafts/`` with the same safety
rules as content briefs (safe stems, atomic replace, no path escapes).
"""

from __future__ import annotations

import os
from pathlib import Path

from ..domain.models import EditorialDecision, EventCandidate, ResearchDossier
from ..textutil import strip_html_tags, strip_markdown_links
from .content_brief import _safe_stem
from .platform_templates import PlatformTemplate, get_platform

_EDITORIAL_ACTION = {
    "ready_to_write": "现在可以报道发生了什么；「去用 / 等等看 / 不用管」三选一（待人工定）。",
    "needs_testing": "先按测试计划亲测，再写体验结论；在此之前只能写「官方宣称」。",
    "watch": "不用管 / 观察：证据不足，等下一轮快照再看。",
    "reject": "不写。",
}

_DECISION_CN = {
    "ready_to_write": "可直接写",
    "needs_testing": "需亲测",
    "watch": "先观察",
    "reject": "不写",
}


def _clean(text: str) -> str:
    return strip_markdown_links(strip_html_tags(text or ""))


def _action_draft(decision: EditorialDecision) -> str:
    return _EDITORIAL_ACTION.get(decision.decision, "待人工定。")


def _fact_lines(facts) -> list:
    lines = []
    for fact in facts:
        source = "（%s）" % fact.source_url if fact.source_url else ""
        lines.append("- [%s] %s %s" % (fact.kind, _clean(fact.text), source))
    return lines


def render_prompt_pack(
    week_key: str,
    event: EventCandidate,
    dossier: ResearchDossier,
    facts,
    decision: EditorialDecision,
    platform: PlatformTemplate,
) -> str:
    """The guardrail bundle for external writing models."""
    lines = []
    lines.append("# 初稿提示包：%s（%s）" % (event.subject, week_key))
    lines.append("")
    lines.append("> 平台：%s（%s）" % (platform.name, platform.title_rule))
    lines.append("> 用途：交给豆包等外部模型出初稿。**护栏在包里，不在 AI 身上**：")
    lines.append("> 任何改写版本必须原样带上「必须出现的事实」与「禁说清单」，终稿前对一遍数字。")
    lines.append("")
    lines.append("## 四问答案")
    lines.append("")
    lines.append("- ① 变化是什么（无术语版）：%s" % _clean(dossier.summary_judgment))
    lines.append("- ② 谁的任务变了：____（agent/人填：人群+任务）")
    lines.append("- ③ 之前 vs 现在：____（agent/人填：前后对比数字，最值钱的一句）")
    lines.append("- ④ 现在该做什么：%s" % _action_draft(decision))
    lines.append("")
    lines.append("## 必须出现的事实（每条带一手来源，只从这里取数）")
    lines.append("")
    lines.extend(_fact_lines(facts))
    lines.append("")
    lines.append("## 禁说清单（改写时逐条自检）")
    lines.append("")
    for item in dossier.forbidden_claims:
        lines.append("- %s" % item)
    lines.append("")
    lines.append("## 标题候选（%s）" % platform.title_rule)
    lines.append("")
    lines.append("- ____（数字前置）")
    lines.append("- ____")
    lines.append("")
    lines.append("## 初稿要求（%s）" % platform.name)
    lines.append("")
    lines.append("- 结构：%s。" % " → ".join(platform.body_sections))
    lines.append("- 正文 ≤%d 字；短句，每段 1-2 行；像朋友提醒，不像新闻稿。" % platform.body_max_chars)
    lines.append("- %s" % platform.tag_rule)
    lines.append("- 结尾：给一个行动建议 + 一句互动。")
    lines.append("")
    lines.append("## 配图模板（照做即可）")
    lines.append("")
    for item in platform.image_spec:
        lines.append("- %s" % item)
    lines.append("")
    lines.append("## 发布与回流")
    lines.append("")
    lines.append("- %s" % platform.source_rule)
    lines.append("- 发布后在选题卡填第二轮反馈（published_url / published_at / outcome / lesson），")
    lines.append("  对 agent 说「同步反馈」落库。")
    lines.append("")
    lines.append("## 受众匹配自查（发前必答，这是「尝菜」那道工序）")
    lines.append("")
    lines.append("- %s" % platform.audience_check)
    lines.append("")
    return "\n".join(lines)


def render_platform_skeleton(
    week_key: str,
    event: EventCandidate,
    dossier: ResearchDossier,
    facts,
    decision: EditorialDecision,
    platform: PlatformTemplate,
) -> str:
    """The per-platform typesetting shell: verified parts placed, expression blank."""
    lines = []
    lines.append("# %s骨架稿：%s（系统生成，表达位待填）" % (platform.name, event.subject))
    lines.append("")
    lines.append("> 用法：agent/豆包只填「待填」空位；**事实与数字禁止改动**；")
    lines.append("> 发布前对照文末禁说清单逐条自查。")
    lines.append("")
    lines.append("## 标题（%s）" % platform.title_rule)
    lines.append("")
    lines.append("____")
    lines.append("")
    lines.append("## 开头结论（1-2 行：谁 + 什么变了 + 多严重，待填）")
    lines.append("")
    lines.append("____")
    lines.append("")
    lines.append("## 数字块（从下方事实清单选 3 个数字，禁止改写数字本身，待填）")
    lines.append("")
    lines.append("- ____")
    lines.append("- ____")
    lines.append("- ____")
    lines.append("")
    lines.append("## 谁受影响（待填；参考受众草稿：%s）" % _clean(dossier.target_audience))
    lines.append("")
    lines.append("____")
    lines.append("")
    lines.append("## 怎么办（待填；参考：%s）" % _action_draft(decision))
    lines.append("")
    lines.append("____")
    lines.append("")
    lines.append("## 来源")
    lines.append("")
    lines.append("%s" % platform.source_rule)
    lines.append("")
    lines.append("## 互动钩子（一句话提问，待填）")
    lines.append("")
    lines.append("____")
    lines.append("")
    lines.append("## 标签（%s）" % platform.tag_rule)
    lines.append("")
    lines.append("#____ #____ #____")
    lines.append("")
    lines.append("## 配图清单（照做即可）")
    lines.append("")
    for item in platform.image_spec:
        lines.append("- %s" % item)
    lines.append("")
    lines.append("## 事实清单（只能从这里取数）")
    lines.append("")
    lines.extend(_fact_lines(facts))
    lines.append("")
    lines.append("## 终审自查 · 禁说清单")
    lines.append("")
    for item in dossier.forbidden_claims:
        lines.append("- %s" % item)
    lines.append("")
    lines.append("## 受众匹配自查（发前必答，这是「尝菜」那道工序）")
    lines.append("")
    lines.append("- %s" % platform.audience_check)
    lines.append("")
    lines.append("## 判定备忘：%s（%s）" % (
        _DECISION_CN.get(decision.decision, decision.decision),
        "、".join(decision.reason_codes),
    ))
    lines.append("")
    return "\n".join(lines)


def _publish(output_root: Path, filename: str, content: str) -> Path:
    target_dir = output_root / "Drafts"
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DraftPublishError("unable to create Drafts directory") from exc
    target = target_dir / filename
    try:
        resolved_root = output_root.resolve()
        resolved_target = target.resolve()
        if not str(resolved_target).startswith(str(resolved_root)):
            raise DraftPublishError("output path escapes the output root")
    except OSError as exc:
        raise DraftPublishError("unable to validate output path") from exc
    tmp = target.with_name(".%s.tmp" % target.name)
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)
    except OSError as exc:
        raise DraftPublishError("unable to write draft file") from exc
    return target


class DraftPublishError(Exception):
    """Raised when a draft file cannot be published safely."""


def publish_prompt_pack(
    output_root: Path,
    week_key: str,
    event: EventCandidate,
    dossier: ResearchDossier,
    facts,
    decision: EditorialDecision,
    platform_key: str = "xiaohongshu",
) -> Path:
    platform = get_platform(platform_key)
    content = render_prompt_pack(week_key, event, dossier, facts, decision, platform)
    filename = "%s - %s - 提示包.md" % (week_key, _safe_stem(event.subject))
    return _publish(output_root, filename, content)


def publish_platform_skeleton(
    output_root: Path,
    week_key: str,
    event: EventCandidate,
    dossier: ResearchDossier,
    facts,
    decision: EditorialDecision,
    platform_key: str = "xiaohongshu",
) -> Path:
    platform = get_platform(platform_key)
    content = render_platform_skeleton(week_key, event, dossier, facts, decision, platform)
    filename = "%s - %s - %s骨架稿.md" % (week_key, _safe_stem(event.subject), platform.name)
    return _publish(output_root, filename, content)
