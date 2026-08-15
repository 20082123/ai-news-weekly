# GS-06 · DeepSeek V4 API 峰谷涨价

- **状态**：草案档案，等待第一用户判定（2026-08-15）
- **第一方证据**：
  - 官方公告（V4-Pro GA）：https://api-docs.deepseek.com/news/news260813/
  - 官方定价页（新旧价目+peak 时段）：https://api-docs.deepseek.com/quick_start/pricing
  - 官方 Changelog：https://api-docs.deepseek.com/updates/
  - 独立报道：InfoWorld（涨价幅度分析）、VentureBeat（Harness 开源）、TechNode（北京时段换算）

## 一句话判断

> DeepSeek 把 V4 API 从"全场最便宜"改成"峰谷分时定价"：8-16 起高峰时段成本约为现价
> 2~4 倍、缓存命中最高约 11 倍，且高峰窗口正好覆盖北京时间两个白天工作段——所有靠
> DeepSeek API 的开发者与自部署用户都要在生效前重新算账。

## 事件与时间线

| 时间 | 事件 |
|---|---|
| 2026-08-13 | DeepSeek-V4-Pro GA（app/web/API，Expert Mode；模型名不变） |
| 2026-08-13 | DeepSeek Harness（dsh）同日开源：Cordis 架构、"Everything is a Plugin"、developer preview（官方明示会有破坏性变更）；GitHub 快照创建当天 107,307★ |
| 2026-08-16 16:00 UTC | 峰谷定价生效：peak = UTC 01:00–04:00 与 06:00–10:00；其余 off-peak 半价 |

## 目标用户与任务

- 谁：用 DeepSeek API 跑 agent/应用的中小开发者、独立创作者、自部署 dsh 的用户。
- 什么任务：重新核算 token 成本；决定错峰调度 / 换模型 / 换供应商 / 接受涨价。

## Claims 与 Evidence

| Claim | 证据 | 可用措辞 |
|---|---|---|
| 新价目：Flash off-peak $0.22/$0.66、peak $0.44/$1.32；Pro off-peak $0.66/$1.98、peak $1.32/$3.96（每 1M token，cache miss） | 官方定价页 | "官方价目表显示……" |
| 简单口径（1M in + 1M out）：Pro 从 $1.305 → off-peak $2.64（≈2x）/ peak $5.28（≈4x） | 官方定价页 + InfoWorld | 可写数字，必须给出口径 |
| cache-hit 单项：Pro $0.003625 → $0.022/$0.044（峰值约 11 倍） | 官方定价页 | 必须注明是 cache-hit 单项 |
| 生效 2026-08-16 16:00 UTC；peak 时段 UTC 01–04、06–10 | 官方 Changelog | 事实 |
| peak 时段 = 北京时间 09:00–12:00、14:00–18:00 | TechNode 换算 | 结构性要点 |
| "off-peak 半价"是相对新 peak 价，不是相对现价 | InfoWorld | contradicts 官方话术 |
| 官方理由："更合理地分配资源"；行业背景：需求暴涨、Anthropic 4 月也涨过 | 官方 Changelog + InfoWorld | 背景，非论点 |

## 限制、反例和未知

1. 分时段对不同负载影响天差地别：批处理可错峰省钱，实时/交互 agent 基本躲不开 peak。
2. 未知：第三方云是否同步调价；V4 之外旧模型是否受影响。
3. Harness 107k★ 是创建当日单点快照，无时间序列；官方自述 developer preview + 破坏性变更。

## 本人测试

- 命题 A（发生了什么+价目+受影响人群）不需要亲测——数字官方可核。
- 命题 B（"你的应用每月成本会涨多少"）：用本人 token 用量 × 新旧单价做测算表。

## Editorial Decision 草案

| 命题 | 判定 | 理由 |
|---|---|---|
| A：峰谷计价事实 + 新旧价目 + 受影响人群 | ready_to_write | 官方发布事实 |
| B：本人/某应用的成本测算 | ready_to_write（数字测算） | 取决于用量数据 |
| C：最便宜神话破灭 / 用户逃离 | 不写（watch） | 情绪与趋势需 Reddit/X 证据 |

## 禁说清单

- 不能只写"暴涨 1100%"（cache-hit 单项；简单口径 2~4 倍）。
- 不能说"所有时段都涨 4 倍"（off-peak 约 2 倍）。
- 不能说"Harness 一天 10 万星=公认好工具"（单点快照）。
- 不把官方"合理分配资源"当结论照抄。

## Golden Set 标注字段（待第一用户填）

| 字段 | 值 |
|---|---|
| example_id | GS-06 |
| event_id | deepseek-v4-peak-pricing |
| event | DeepSeek V4 API 峰谷计价 8-16 生效 |
| content_proposition | 见上表命题 A |
| change_type | Economics / Access |
| primary_audience | （草案）靠 DeepSeek API 的中小开发者/独立创作者 |
| job_to_be_done | 生效前重算 token 成本并决策 |
| primary_evidence | api-docs.deepseek.com 定价页 + Changelog |
| missing_evidence | 第三方云同步调价情况；本人用量数据 |
| expected_qualification | research |
| expected_editorial | （草案）ready_to_write |
| why | 待第一用户 |
| content_outcome | 待第一用户 |
