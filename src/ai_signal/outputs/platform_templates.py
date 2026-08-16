"""Per-platform publishing templates (DEC-021).

One deterministic "发稿说明书" per platform: title rules, body skeleton,
tag rules, image spec and source placement. The system renders skeletons
from these templates; expression (the actual words) is filled by the agent
or an external model - the system itself never writes prose.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple


class PlatformTemplateError(ValueError):
    """Raised when a platform key is unknown."""


@dataclass(frozen=True)
class PlatformTemplate:
    key: str
    name: str
    title_max_chars: int
    title_rule: str
    body_sections: Tuple[str, ...]
    body_max_chars: int
    tag_rule: str
    tag_pattern: Tuple[str, ...]
    image_spec: Tuple[str, ...]
    source_rule: str
    review_rule: str


XIAOHONGSHU = PlatformTemplate(
    key="xiaohongshu",
    name="小红书",
    title_max_chars=20,
    title_rule="标题≤20字；数字前置；一句话说清「谁 + 什么变了 + 多严重」。",
    body_sections=("开头结论", "数字块", "谁受影响", "怎么办", "来源", "互动钩子"),
    body_max_chars=400,
    tag_rule="3-5 个标签：1 主题词 + 1 人群词 + 1-2 场景词；不用营销词。",
    tag_pattern=("主题词", "人群词", "场景词"),
    image_spec=(
        "图1 封面（3:4 竖版）：官方页截图，红框圈出最关键的 1 个数字与生效时间；图上配字 ≤12 字。",
        "图2：把「变化前后」两行数字并排截出，红框标注，配一句对比（如「避开X，省一半」）。",
        "文字规则：每张图一句话，字号大、不用术语；图上的数字必须与正文完全一致。",
    ),
    source_rule="正文末尾写「来源：官方 <站点> <路径>」；完整链接放评论区。",
    review_rule="发布前对照禁说清单逐条自查；把正文数字与官方页再对一遍。",
)

PLATFORM_CATALOG: Dict[str, PlatformTemplate] = {
    XIAOHONGSHU.key: XIAOHONGSHU,
}


def get_platform(key: str) -> PlatformTemplate:
    if key not in PLATFORM_CATALOG:
        raise PlatformTemplateError("unknown platform: %r" % key)
    return PLATFORM_CATALOG[key]
