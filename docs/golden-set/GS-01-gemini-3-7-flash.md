# GS-01 · Gemini 3.7 Flash（coding/agent 工作马 + 入门定价）

- **状态**：草案档案，等待第一用户判定（2026-08-15）
- **第一方证据**（经 Exa 抓取摘要；blog.google 直接访问被反爬）：
  - 官方博客：https://blog.google/innovation-and-ai/models-and-research/gemini-models/introducing-gemini-3-7-flash/
  - 旁证：https://9to5google.com/2026/08/13/gemini-3-7-flash-launch/ 、
    https://decrypt.co/375580/google-openai-super-fast-ai-models-gemini-flash-gpt-ultrafast

## 一句话判断

> Google 三周内连发两代 Flash，把"便宜、够用、能跑 agent"的工作马模型推成正式产品：
> 入门价 $0.75/$3.75（每 1M token）执行到 2026 年底，之后翻倍——现在尝鲜和年底前锁成本的
> 决策是同一条新闻。

## 事件与时间线

| 时间 | 事件 |
|---|---|
| 2026-08-13 | Gemini 3.7 Flash GA：1M 输入 / 64K 输出，文本+图像+视频+音频+PDF，支持工具调用与电脑操作 |
| 2026-08-13 | 定价：入门 $0.75/$3.75 至 2026-12-31；2027-01-01 起 $1.50/$7.50 |
| 2026-08-13 | 同日接入 Spark（AI Pro/Ultra，160+ 国）、AI Studio、Android Studio、Antigravity、Enterprise；8-13 起进 GitHub Copilot |

## 目标用户与任务

- 谁：用便宜模型跑批量/agent 任务的独立开发者、小团队；Copilot 用户。
- 什么任务：选一个"日常够用且便宜"的模型；在 12-31 前评估锁价窗口。

## Claims 与 Evidence

| Claim | 证据 | 可用措辞 |
|---|---|---|
| 3.7 Flash GA，定位 coding/agent 工作马，三周前刚发 3.6 Flash | 官方博客 | "官方称……" |
| 1M 输入/64K 输出、多模态、工具调用、电脑操作 | 官方博客/Decrypt | 事实 |
| 入门价 $0.75/$3.75 至 12-31，之后 $1.50/$7.50 | 官方博客 | 数字可核（定价页再核） |
| 已进 Copilot（8-13） | GitHub Changelog | 事实 |
| "比 3.6 Flash 强多少"的第三方评测 | 无 | 未知项 |

## 限制、反例和未知

1. 性能提升目前主要来自官方口径；独立横评（SWE-bench 等）尚缺。
2. 12-31 后价格翻倍是**确定事实**，不是"可能涨"。
3. 未知：Spark 订阅配额与 API 限额是否随新模型调整。

## 本人测试

- 命题 A（发布事实+定价）不需要亲测 → ready_to_write。
- 命题 B（"3.7 Flash 实际跑我的任务比 3.6 强"）→ needs_testing（个人任务横评）。

## Editorial Decision 草案

| 命题 | 判定 |
|---|---|
| A：3.7 Flash 发布 + 入门价 + 年底涨价时间表 | ready_to_write |
| B：个人任务实测对比 | needs_testing |

## 禁说清单

- 不说"三周就换代=Google 慌了"（节奏是事实，归因需证据）。
- 不把官方性能口径当独立结论。

## Golden Set 标注字段（待第一用户填）

| 字段 | 值 |
|---|---|
| example_id | GS-01 |
| event_id | gemini-3-7-flash |
| event | Gemini 3.7 Flash GA + 入门定价 |
| content_proposition | 工作马模型换代 + 年底锁价窗口 |
| change_type | Capability + Economics |
| primary_audience | 批量/agent 任务开发者、Copilot 用户 |
| job_to_be_done | 选便宜够用的模型并评估锁价 |
| primary_evidence | 官方博客 |
| missing_evidence | 独立横评；配额调整信息 |
| expected_qualification | research |
| expected_editorial | （草案）ready_to_write |
| why | 待第一用户 |
| content_outcome | 待第一用户 |
