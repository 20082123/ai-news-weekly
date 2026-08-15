# Golden Set 工作区

> 状态：`进行中`（2026-08-15 起）。目标 10–20 个真实 AI 变化的第一用户标注；
> 完成门槛见 [docs/04-GOLDEN-EXAMPLES.md](../04-GOLDEN-EXAMPLES.md) 第 7 节。
> 本目录所有档案均为**草案**：事实与证据来自第一方链接，判定列等待第一用户填写。

## 已有档案

| ID | 事件 | 类型(草案) | 我的草案 editorial | 第一用户判定 |
|---|---|---|---|---|
| [GS-06](GS-06-deepseek-v4-pricing.md) | DeepSeek V4 峰谷涨价（8-16 生效） | Economics | ready_to_write | 待判 |
| [GS-12](GS-12-hax-v0.3.0.md) | hax v0.3.0 设计取舍 / 体验宣称 | Tool/Workflow | A=ready_to_write, B=needs_testing | 初判：不吸引（见档案内记录） |

## 标注填写表（20 个候选）

只填最后两列，或整行改判。判定词固定四选一：`ready_to_write / needs_testing / watch / reject`。

| ID | 事件 | 类型(草案) | 第一方证据 | 我的草案 | 你的判定 | 为什么 |
|---|---|---|---|---|---|---|
| GS-01 | Gemini 3.7 Flash | Capability+Economics | blog.google | ready_to_write |  |  |
| GS-02 | GPT-5.6 Sol Ultrafast 预览 | Capability | openai.com | watch |  |  |
| GS-03 | MAI-Code-1.1 进 Copilot | Capability | github.blog | ready_to_write |  |  |
| GS-04 | Claude 全系水印+检测 API | Capability | anthropic.com | ready_to_write |  |  |
| GS-05 | DeepSeek V4-Pro GA + Harness 开源 | Capability | api-docs.deepseek.com | ready_to_write（体验命题 needs_testing） |  |  |
| GS-06 | DeepSeek 峰谷涨价 | Economics | api-docs 定价页 | ready_to_write |  |  |
| GS-07 | ChatGPT Business $125 席位 | Economics | help.openai.com | ready_to_write |  |  |
| GS-08 | Luna/Terra 降价 | Economics | openai.com | ready_to_write |  |  |
| GS-09 | Claude Sonnet 5 不涨价 | Economics | platform.claude.com | ready_to_write |  |  |
| GS-10 | Copilot 周更：会话/工作树/插件 1.0 | Workflow | github.blog | ready_to_write |  |  |
| GS-11 | MS Agent Framework stable | Workflow | devblogs.microsoft.com | ready_to_write |  |  |
| GS-12A | hax v0.3 设计取舍 | Workflow | hax releases | ready_to_write |  |  |
| GS-12B | hax "更好用更稳定" | Workflow | 同上（不足） | needs_testing |  |  |
| GS-13 | KiroCrew v0.2 自我改进 | Workflow | kirodotdev releases | needs_testing |  |  |
| GS-14 | 一周三模型进 Copilot | Ecosystem | github.blog | ready_to_write |  |  |
| GS-15 | gh-aw PureLock 自跑 Agent | Ecosystem | github.github.com | watch |  |  |
| GS-16 | agents-python v0.21 常规迭代 | 无实质事件 | releases 页 | watch |  |  |
| GS-17 | earendil-works/pi 机器人 release | 无事件 | releases 页 | reject |  |  |
| GS-18 | oh-my-codex-remix（1★营销） | 无证据 | 仓库页 | reject |  |  |
| GS-19 | Aider 停更追踪 | 追踪项 | 仓库页 | watch |  |  |
| GS-20 | autogen/langgraph 元数据薄 | 观察项 | 仓库页 | watch |  |  |

覆盖检查：Capability ✅ / Economics ✅ / Workflow ✅ / Ecosystem ✅；
坏例（watch/reject）≥4 ✅；ready_to_write 与 needs_testing 候选充足。

## 判定词三套不混用

- qualification：`research / watch / reject`（2C1 机器判定）；
- editorial：`ready_to_write / needs_testing / watch / reject`（**本表用这套**）；
- 人工反馈：`adopted / parked / rejected`（素材采用时用）。
