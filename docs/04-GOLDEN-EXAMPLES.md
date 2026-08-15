---
title: AI Signal Agent 黄金样例与验收标准
owns: 好信号、好研究档案、坏结果和编辑状态的可操作样例
does_not_own: 产品定位、阶段排期、当前进度、生产数据
status: draft_needs_user_validation
last_updated: 2026-08-15
---

# AI Signal Agent 黄金样例与验收标准

这份文档回答：**系统最后应该给我什么，什么结果即使代码正确也必须被拒绝。**

当前版本先定义质量契约和少量种子样例。下一步需要第一用户共同选择 10～20 个真实 AI 变化完成 Golden Set；在此之前，不能把任何自动评分称为已经准确。

## 1. 三层输出不能混用

### 1.1 Candidate：是否值得继续研究

Candidate 只是低成本入口。它可以是一条 GitHub 仓库、官方公告、网页或社区讨论。

合格 Candidate 只需说明：

- 发现了什么对象；
- 为什么可能值得继续；
- 当前缺少哪些证据。

它不是稿件，也不能直接判断“好用”或“值得买”。

### 1.2 Signal Card：是否值得现在花几分钟

一张合格 Signal Card 必须让用户在 30～60 秒内回答：

- 具体发生了什么；
- 为什么可能与某个任务有关；
- 有什么第一方证据；
- 还不知道什么；
- 建议深挖、观察还是忽略。

### 1.3 Research Dossier：能负责任地讲什么

合格 Dossier 至少包含：

1. 事件、版本与时间线；
2. 一个主要受众及一个具体任务；
3. 事实—证据对应关系；
4. 官方宣称与已验证事实分开；
5. 一个限制、反例或未知项；
6. 需要本人测试时，给出可执行测试任务；
7. 可以拍摄、截图或制作对比的证据资产；
8. 清楚的 Editorial Decision 与理由；
9. 禁止直接表达的结论。

## 2. Signal Card 标准模板

```markdown
# [建议深挖 / 观察 / 忽略] 事件名称

发生了什么：
为什么现在值得看：
影响谁的什么任务：

第一方证据：
- URL + 时间/版本

已有补充或反证：
- URL + 它证明或不能证明什么

尚未确认：
- ...

建议动作：
- 深挖 / 观察 / 忽略
- 原因：...
```

## 3. Research Dossier 标准模板

```markdown
# Research Dossier：事件名称

## 一句话判断
一句可争论、可验证的内容命题，而不是产品简介。

## 事件与时间线
- 日期 / 版本 / 具体变化 / 来源

## 目标用户与任务
- 谁：
- 在什么情境：
- 原本怎样做：
- 这次变化可能改变什么：

## Claims 与 Evidence
| Claim | 类型 | 证据 | supports / contradicts / contextual | 可用措辞 |

## 限制、反例和未知
- ...

## 本人测试
- 是否必须：
- 环境与版本：
- 任务：
- 记录：成功率、耗时、成本、人工接管、失败点

## 可视化资产
- Release/文档截图：
- 屏幕录制：
- 对比表或流程图：

## Editorial Decision
- ready_to_write / needs_testing / watch / reject
- reason codes：

## 禁说清单
- ...
```

## 4. 种子样例

以下样例用于解释判断方式。只有标注为“真实种子”的事实样例可进入后续 Golden Set；示意样例不能被当作现实新闻发布。

### G-001：最近更新的低信息仓库

**类型：来自真实低质量批次的抽象坏例**  
**预期结果：`reject`，不得生成 MaterialPack**

```text
已知：
- 仓库刚刚 pushed；
- 0～几颗 Stars；
- description 写着 AI Agent；
- 有语言和 topics。

缺少：
- 具体 Release；
- 本周发生的实质变化；
- README 中明确的任务与使用方式；
- Demo、Docs、独立用户或可测试路径。
```

拒绝原因：`pushed_at` 只说明发生过推送，作者自填 topic 不证明质量，仓库元数据也不能回答目标用户为何在意。

该样例解释了为什么旧 2B3 的 10 份中文文件即使字段可追溯，也不构成好素材。

### G-002：Hax v0.3 的明确版本变化

**类型：真实种子，后续需要第一用户复核**  
**Candidate Qualification：`research`**  
**Editorial：报道版本与设计思想可为 `ready_to_write`；声称实际好用必须 `needs_testing`**

第一方入口：

- [Repository](https://github.com/OleksandrChekhovskyi/hax)
- [Releases](https://github.com/OleksandrChekhovskyi/hax/releases)
- [v0.3.0 Release](https://github.com/OleksandrChekhovskyi/hax/releases/tag/v0.3.0)
- [Philosophy](https://github.com/OleksandrChekhovskyi/hax/blob/master/docs/philosophy.md)

可负责任地研究的命题：

> 一个终端 Agent 是否可以通过“单一原生程序、本地模型优先和 Unix 组合”换取更简单的使用边界，而不复制复杂插件生态？

为什么它比仓库简介更像内容：

- 有 v0.3 的明确时间与版本锚点；
- 有安装和任务处理变化；
- 项目明确说明了不采用 MCP、插件/hooks 等设计取舍；
- 可以形成“复杂生态 vs 简单工具”的内容张力；
- 可以设计真实终端任务验证安装、成功率、速度和失败恢复。

表达边界：

- 可以说“项目文档选择了……”；
- 可以说“v0.3 Release 写明新增/改变了……”；
- 没有本人测试前，不能说“更好用、更稳定、更省时间”；
- 只有 GitHub 来源时，不能说“普通用户正在大量采用”或“全网爆火”。

Golden Set 的标注单位是**一个 Event 下的一个内容命题**。同一个 Event 可以重复出现，
但不同命题必须拆成不同 example，避免一行同时拥有两个 Editorial 状态。

完整标注示例 A（当前为待用户复核的预期标签）：

| 字段 | G-002A 预期值 |
| --- | --- |
| `example_id` | `G-002A` |
| `event_id` | `hax-v0.3.0`（Golden Set 内的人工稳定别名） |
| `content_proposition` | Hax v0.3.0 与其设计文档体现了“用更少扩展机制换取简单边界”的工具取舍 |
| `change_type` | `Tool / Workflow` |
| `primary_audience` | 在终端完成 AI 辅助任务的技术型知识工作者 |
| `job_to_be_done` | 选择一个边界清晰、偏本地和终端组合的 Agent 工具 |
| `primary_evidence` | v0.3.0 Release + Philosophy 文档 |
| `missing_evidence` | GitHub 外部采用证据；但不是当前“设计取舍”命题的阻塞项 |
| `expected_qualification` | `research` |
| `expected_editorial` | `ready_to_write` |
| `why` | 具体版本与官方设计文档足以支持“项目选择了什么”，不声称实际体验更好 |
| `content_outcome` | `not_yet_used` |

完整标注示例 B：

| 字段 | G-002B 预期值 |
| --- | --- |
| `example_id` | `G-002B` |
| `event_id` | `hax-v0.3.0` |
| `content_proposition` | Hax 比复杂 Agent 框架更容易安装、更稳定并更适合日常终端任务 |
| `change_type` | `Tool / Workflow` |
| `primary_audience` | 在终端完成 AI 辅助任务的技术型知识工作者 |
| `job_to_be_done` | 用 Agent 完成真实终端任务，并在失败时恢复 |
| `primary_evidence` | v0.3.0 Release + Philosophy 文档只能证明设计与发布事实 |
| `missing_evidence` | 本人成功率、速度、失败恢复、成本和对照工具测试 |
| `expected_qualification` | `research` |
| `expected_editorial` | `needs_testing` |
| `why` | “更容易、更稳定、更适合”是体验型结论，官方文档不能单独证明 |
| `content_outcome` | `not_yet_used` |

这两张表展示了实际标注粒度：事件可以共用，但每一行只保留一个主类型、一个主要
受众、一个内容命题和一个 Editorial 状态。

### G-003：官方产品的价格或权限变化

**类型：示意结构，不代表某个现实产品已经发生此事**  
**预期 Editorial：核心发布事实可为 `ready_to_write`**

```text
事件：某 AI 办公产品改变免费额度或订阅价格。
第一方证据：官方公告 + 当前定价页 + 生效日期。
具体受众：每天依赖该工具的独立创作者或小团队。
工作影响：每月成本、可用次数或迁移决策发生变化。
未知：实际稳定性与替代方案质量。
```

为什么不一定需要本人先测试：价格、生效日期和官方权限是可由一手文档确认的发布事实。可以报道发生了什么和谁需要检查成本，但不能据此宣称产品体验更好。

### G-004：官方宣称 Agent 能自动完成完整工作流

**类型：示意结构**  
**预期 Editorial：`needs_testing`**

```text
官方宣称：Agent 可自动完成“读取资料 → 填写系统 → 发送结果”。
已有证据：官方 Demo 和 Docs。
缺口：真实账号权限、异常页面、2FA、失败恢复、成本。
本人测试：设计三个任务，记录成功率、耗时、接管次数和失败点。
```

可以 `ready_to_write` 地报道“官方发布了该能力”；若标题或结论是“普通人现在可以交给它全自动完成”，就必须先测试。

### G-005：单条高赞社区吐槽

**类型：示意坏例**  
**预期结果：作为反向线索 `watch`，不能直接形成总体结论**

一条 Reddit 或 X 帖子可以证明“有人在某版本和环境遇到这个问题”，不能证明“用户普遍都失败”。应进一步核对版本、环境、维护者回应和其他独立案例。

### G-006：多个产品共同出现同一工作模式

**类型：示意结构**  
**预期结果：可能形成 Ecosystem Event，需多源研究**

只有当多个独立产品、版本或用户行为都出现相同变化，且能说明它如何改变具体工作任务时，才能形成“变化背后的变化”。单个仓库使用同一关键词不构成市场转向。

## 5. 编辑状态的硬边界

### `ready_to_write`

必须满足：

- 事件、时间与具体变化清楚；
- 核心事实有第一方来源；
- 受影响的人和任务具体；
- 已知限制与未知项明确；
- 内容命题不依赖尚未完成的体验型结论。

### `needs_testing`

当核心命题包含以下表达时通常需要测试：

- 好用、稳定、提效、节省多少时间；
- 适合某类真实工作；
- 可以无人值守；
- 成本低、部署简单；
- 能替代现有工具或人工流程。

### `watch`

适用于可能重要，但目前缺少时间锚点、第一方证据、受众任务或独立验证的事件。

### `reject`

满足任一项即可拒绝：

- 没有实质变化，只有更新时间、Stars 或转发量；
- 找不到第一方原始来源；
- 与目标受众的关系只能用“AI 很重要”解释；
- 重复转述同一公告，没有新增确认、反证或体验；
- 无法提出具体任务或负责任的内容命题；
- 为了满足数量而被强行填入。

## 6. Golden Set 标注字段

接下来选择的每个真实样例至少记录：

| 字段 | 含义 |
| --- | --- |
| `example_id` | 稳定样例编号 |
| `event_id` | 同一事件在多个内容命题之间共享的稳定别名 |
| `event` | 具体发生的变化 |
| `content_proposition` | 本行要判断的唯一内容命题 |
| `change_type` | Capability / Workflow / Economics / Ecosystem |
| `primary_audience` | 一个主要受众 |
| `job_to_be_done` | 一个具体任务 |
| `primary_evidence` | 第一方证据 |
| `missing_evidence` | 当前关键缺口 |
| `expected_qualification` | research / watch / reject |
| `expected_editorial` | ready_to_write / needs_testing / watch / reject |
| `why` | 第一用户的判断理由 |
| `content_outcome` | adopted / parked / rejected / not_yet_used |

## 7. Golden Set 完成门槛

- 10～20 个真实样例；
- 至少 4 个应拒绝或观察的坏例；
- 四类变化均有覆盖；
- 至少 2 个 `ready_to_write`、2 个 `needs_testing`；
- 第一用户能解释每个预期状态，而不是只给一个分数；
- 未来策略在改动前能用这些样例回放；
- 自动结果与 Golden Set 不一致时，系统显示差异，不偷偷修改标签。

在第一用户完成复核前，本文件状态保持 `draft_needs_user_validation`。
