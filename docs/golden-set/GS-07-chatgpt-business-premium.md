# GS-07 · ChatGPT Business 新增 $125 Premium 席位

- **状态**：草案档案，等待第一用户判定（2026-08-15）
- **第一方证据**（经 Exa 抓取摘要；help.openai.com 直接访问被反爬）：
  - 官方 Release Notes：https://help.openai.com/en/articles/6825453-chatgpt-release-notes
  - 旁证：https://the-decoder.com/openai-introduces-125-premium-seats-for-chatgpt-business-as-agentic-ai-burns-through-more-tokens/ 、
    https://www.techtimes.com/articles/323905/20260811/chatgpt-business-adds-125-premium-seat-power-users-hitting-five-hour-cap.htm

## 一句话判断

> OpenAI 用 $125/人/月的 Premium 席位补上了 Business（$25）与 Enterprise（六位数合同）之间
> 的空档：5 倍容量、无 5 小时上限——本质是承认"agent 烧 token"后，按用量重新分层收费。

## 事件与时间线

| 时间 | 事件 |
|---|---|
| 2026-08-11 | 官宣 Premium 席位：$125/人/月（年付 $100）；5 倍容量、无 5 小时上限、周重置；与 Standard 混排同工作区 |
| 2026-08-19 | 起新增席位改为即时按比例计费（不再等下个账期） |
| 2026-08-20 | waitlist 截止：前 1 万工作区，每个 Premium 席位送 $100 credits（最多 5 席 = $500） |
| 同日发布 | 新 Pro 套餐 $100/月（无限 GPT-5.4 + 限时 10x Codex 用量）；$200 Pro 至 5-31 促销不变；Plus 用量重平衡 |

## 目标用户与任务

- 谁：把 ChatGPT Business 当工作流/agent 用的团队与自由职业者；卡在 5 小时上限的重度用户。
- 什么任务：算清"上 Premium 还是上 Enterprise"；8-20 前决定是否抢 waitlist credits。

## Claims 与 Evidence

| Claim | 证据 | 可用措辞 |
|---|---|---|
| Premium $125/月（$100 年付）、5x 容量、无 5 小时上限 | 官方 Release Notes | 事实 |
| Standard 维持 $25/月（$20 年付）；两档可混排 | 官方/Decoder | 事实 |
| waitlist 8-20 截止，每席 $100 credits（上限 $500） | TechTimes | 时效性事实 |
| 8-19 起新增席位即时按比例计费 | 官方/Decoder | 财务影响要点 |
| 背景：agent 工作流大幅推高 token 消耗 | Decoder | 背景，非论点 |
| $100 Pro 套餐：无限 GPT-5.4 + 限时 10x Codex | 官方 Release Notes | 事实 |

## 限制、反例和未知

1. 数字来源官方，但"5 倍容量"的实际换算（credits 如何映射）需要对照官方帮助页确认。
2. 未知：Premium 是否会后续放开到个人订阅；waitlist 先到先得窗口很短。

## 本人测试

- 命题 A（定价与权益事实）不需要亲测 → ready_to_write。
- 命题 B（"Premium 值不值"）依赖使用量测算，非体验测试。

## Editorial Decision 草案

| 命题 | 判定 |
|---|---|
| A：Premium 席位定价、权益与 8-19 计费变更 | ready_to_write |
| B：值不值 / 该不该抢 waitlist | needs_testing（个人用量测算） |

## 禁说清单

- 不写"OpenAI 全面涨价"（Standard 未涨；这是新增分层）。
- 不把 5 小时上限的体验推断写死（未实测）。

## Golden Set 标注字段（待第一用户填）

| 字段 | 值 |
|---|---|
| example_id | GS-07 |
| event_id | chatgpt-business-premium-seat |
| event | ChatGPT Business 新增 $125 Premium 席位 |
| content_proposition | 定价分层 + 计费变更 + waitlist 窗口 |
| change_type | Economics / Access |
| primary_audience | Business 订阅团队与重度用户 |
| job_to_be_done | 在 8-20 前决定席位与成本方案 |
| primary_evidence | 官方 Release Notes |
| missing_evidence | credits 换算细则 |
| expected_qualification | research |
| expected_editorial | （草案）ready_to_write |
| why | 待第一用户 |
| content_outcome | 待第一用户 |
