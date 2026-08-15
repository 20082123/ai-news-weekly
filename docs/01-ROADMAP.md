---
title: AI Signal Agent 阶段路线图
owns: 阶段定义、阶段效果、验收条件、进入下一阶段的门槛
does_not_own: 产品定位、当天进度、详细代码结构、数据库字段
status: active
last_updated: 2026-08-15
---

# AI Signal Agent 阶段路线图

这份文档回答：**项目分成哪些阶段，每一阶段做完后用户究竟能得到什么。**

阶段不能只按“新增了多少代码”判定完成。每一阶段都必须分别记录工程、真实运行和产品效果。

## 1. 完成状态的统一规则

### 状态词

- `planned`：已经定义，但尚未开始；
- `in_progress`：正在工作，尚未通过全部验收；
- `verified`：有对应层面的验收证据；
- `paused`：有意停止，等待前置决策或验证；
- `superseded`：保留历史与兼容性，但不再是推荐主线。

### 三种“完成”必须分开

| 维度 | 证明什么 | 不能证明什么 |
| --- | --- | --- |
| 工程完成 | 代码、测试、安全与回滚满足规格 | 输出对用户有价值 |
| 真实运行 | 在真实数据与真实环境中跑通 | 选择和内容质量合格 |
| 产品验证 | 第一用户愿意采用，且达到明确效果 | 工程适合扩展为多用户产品 |

没有验收证据的层面不能标记为 `verified`。

## 2. 历史实现地图

历史编号保留用于对应 Git 提交和测试，不再用它直接表达未来产品优先级。

| 历史阶段 | 工程状态 | 真实运行 | 产品效果 | 当前定位 |
| --- | --- | --- | --- | --- |
| V0 邮件周报 | `verified` | `verified`，线上继续运行 | 能发送旧周报，但不满足新的工作影响 North Star | 生产基线 |
| Phase 1 基础设施 | `verified` | 离线验证 | 无直接用户内容效果 | 保留的工程地基 |
| 2A GitHub fixture 采集 | `verified` | fixture 已验证 | 无真实素材效果 | Source Adapter 基础 |
| 2B1 GitHub REST 采集 | `verified` | 曾真实采集 10 条仓库数据 | 召回质量未验证 | GitHub Sensor 基础 |
| 2B2 仓库直接素材化 | `verified` | 已真实生成素材 | `failed`：Repository 被错误等同于 Event | `superseded` 原型 |
| 2B3 中文 A–F 输出 | `verified` | 已生成 10 份中文文件 | `failed`：更可读，但素材质量低 | `superseded` 展示原型 |
| 2C1 Candidate Qualification | `verified`，423 项总测试通过 | 尚未对真实 Vault 数据运行 | 尚未改善发现或素材质量 | 可保留的 GitHub 候选层 |
| 2C2 GitHub Discovery Policy | `in_progress`（2C2-A 进行中） | 无 | 无 | GitHub-specific Discovery Policy Adapter（DEC-013） |

关键解释：

- 2B2/2B3 不是白做。它们验证了证据绑定、Markdown 输出和反馈基础，但证明了“仓库不能直接成为素材”。
- 2C1 不是内容系统。它只回答“这个 GitHub 仓库值不值得继续花成本研究”。
- 当前 423 项测试是工程证据，不是内容质量证据。

### 2.1 修正后的全局路线图（技术主线，2026-08-15 定版）

```text
2C1 GitHub Repository Candidate Qualification     ✅ 完成
↓
2C2 GitHub Discovery Policy Adapter               in_progress（GitHub-specific，只输出 GitHub Research Queue）
↓
2C3 Source-independent Discovery Gate
     + Signal Taxonomy
↓
2D1 Source-neutral Research Core / ResearchDossier
↓
2D2 GitHub Research Adapter
↓
2D3 Official/Web Discovery + Evidence Adapter
↓
2E Audience Impact + Editorial Decision
↓
2F Content Brief / Test Plan / B站母内容 / 平台复用
↓
Phase 3 Feedback / Evaluation / Daily & Weekly Operations
↓
Phase 4 Reddit for User Reality / X for Early Signals
```

Official/Web 已提前到 Reddit/X 之前，因为它能提供官方公告、文档、Release
notes、定价、使用限制与官方 Demo，有助于产生无需本人重复测试的
`READY_TO_WRITE` 内容。

2C2 定位再确认（DEC-013）：它是 GitHub-specific 的 policy adapter——四条
GitHub Discovery Lane（Watchlist / Mature / Emerging / Ecosystem）的查询策略、
scope/spec 防串线、候选级去重与研究预算。它的输出只是 GitHub Research
Queue，不是全局 Signal，也不是 Event。2C2 完成后停止连续扩展 GitHub，先
实施 2C3。

## 3. 从现在开始的结果型路线图

### Stage A：产品契约与 Golden Set

**状态：`in_progress`**  
**目标窗口：2026-08-15 ～ 2026-08-21**

#### 要解决的问题

在继续编码前，统一“什么是好 Signal、好 Dossier 和合格内容机会”，防止某个数据源再次绑架产品。

#### 工程交付

- 建立产品、路线图、状态、架构、黄金样例和决策文档；
- 为 10～20 个真实 AI 变化建立人工标注样例；
- 记录预期变化类型、受众任务、证据要求和编辑状态；
- 2C2 只按 DEC-013 的 GitHub-specific 边界推进，不做全局 Signal/Event 层与来源扩展。

#### 用户可见效果

用户无需阅读代码，即可回答：项目为什么做、做到哪、下一阶段带来什么、什么输出才算好。

#### 验收标准

- 六份控制文档互不重复定义职责；
- 一个无本会话背景的读者能从文档正确回答项目核心问题；
- 第一用户审核并认可 10～20 个 Golden examples；
- Golden Set 至少覆盖 Capability、Workflow、Economics、Ecosystem，以及 `ready_to_write / needs_testing / watch / reject`；
- 任何未来阶段都有用户效果、非目标和停止条件。
- 根据 Golden Set 新建并确认 `06-STAGE-B-SPEC.md`，明确首个纵切、Event/Dossier
  契约、schema/migration、CLI、版本策略和兼容测试；本文件不提前替它决定实现细节。

#### 本阶段不做

不接 X/Reddit，不做自动进化，不开发多用户界面，不继续完善 GitHub 排行与 A–F 模板。

#### 简历价值

体现需求纠偏、领域建模、验收设计和把工程指标与产品指标分离的能力。

#### 进入下一阶段的门

只有第一用户确认文档和 Golden Set 能代表真实选择标准，且确认由这些样例推导出的
`06-STAGE-B-SPEC.md`，才进入 Stage B。没有实施规格时不得开始 migration、领域模型或 CLI 编码。

#### 停止条件

如果 10～20 个真实样例仍无法形成一致判断，停止编码并进一步收窄受众或内容命题；
不能用一个新评分公式掩盖分歧。

---

### Stage B：Event-level Research MVP（GitHub + Official/Web）

**状态：`planned`**  
**目标窗口：2026-08-22 ～ 2026-08-26**

#### 要解决的问题

把“仓库候选”或“官方公告”转换成围绕同一真实变化组织的 Research Dossier，而不是把来源摘要直接变成素材。

#### 输入与输出

```text
GitHubRepositoryCandidate / OfficialAnnouncementCandidate
→ Canonical Event Candidate
→ Event Confirmation Gate
→ Confirmed Canonical Event
→ Signal Card
→ 用户选择深挖
→ Research Dossier
```

#### 工程交付

- 保留 2C1 作为 GitHub-specific Candidate 层；
- 接入 GitHub README、最新 Release 与必要 Docs；
- 增加 Official/Web 的一手证据读取能力；
- 建立 source-independent 的 Event 与 Research Dossier 契约；
- Claim 绑定明确来源，并区分事实、官方宣称、反证和未知项；
- 2B `materialize github` 保留兼容，但不进入推荐主路径。

#### 用户可见效果

用户拿到的不是“某个仓库有多少 Stars”，而是：发生了什么、为什么与具体任务有关、有哪些证据、还缺什么、是否值得继续。

#### 验收标准

- 使用至少 5 个真实事件运行；
- 仓库不再自动等于 Event；
- 同一事件的 GitHub 与官方网页能聚合到一份 Dossier；
- 核心事实全部可回到第一方来源；
- 每份 Dossier 至少有一个具体用户任务、一个限制或未知项；
- Golden Set 中应当拒绝的垃圾仓库不能进入最终 Dossier；
- 单个来源失败时结果明确降级，不静默伪装完整。

#### 本阶段不做

不接 Reddit/X，不自动生成最终稿，不做复杂趋势模型或向量数据库。

#### 简历价值

可展示多源信息抽取、实体/事件归一、Claim–Evidence、失败降级和可审计 Research Agent。

#### 进入下一阶段的门

第一用户认为至少 2 份 Dossier 相比从零搜索节省明显时间，并且能据此做出继续/放弃判断。

#### 停止条件

如果 5 个真实事件中没有至少 2 份 Dossier 被认为有用，回到 Golden Set、Event
边界或证据选择修正，不进入 Editorial，也不增加 Reddit/X。

---

### Stage C：Editorial Decision 与可写输出

**状态：`planned`**  
**目标窗口：2026-08-27 ～ 2026-08-29**

#### 要解决的问题

研究资料齐全不等于值得发布。本阶段负责把证据状态转换成清晰的编辑决定。

#### 工程交付

- 实现 `ready_to_write / needs_testing / watch / reject`；
- 每个决定都有 reason codes 与缺失证据；
- 生成可由用户编辑的 Markdown Dossier/选题骨架；
- 发布事实与体验结论采用不同证据门槛；
- 保留用户采用、暂存和拒绝原因。

#### 用户可见效果

用户能迅速知道：哪条现在可以写，哪条必须亲测，哪条不用浪费时间。

#### 验收标准

- 至少产生 1 个真实 `ready_to_write` 与 1 个真实 `needs_testing`；
- 不亲测时，只能报道有一手证据支持的发布事实与边界；
- 声称“好用、提效、稳定、适合某任务”时必须有本人测试或合格独立证据；
- 用户能在 30 分钟内从一份合格 Dossier 形成可修改稿件；
- 没有合格内容时允许输出 0 条。

#### 本阶段不做

不自动发布，不保证流量，不把平台改写模板当作素材质量。

#### 简历价值

可展示规则与模型协同、证据门控、人机协作和可解释决策。

#### 进入下一阶段的门

至少一份结果被本人真正采用为内容。

#### 停止条件

如果没有任何结果被采用，先区分问题来自选题、证据、命题还是输出形式；在原因没有
被记录并修正前，不自动生成更多稿件或接入发布平台。

---

### Stage D：8 月底可用、可演示版本

**状态：`planned`**  
**截止：2026-08-31**

#### 要解决的问题

把已验证能力收敛为一条能现场展示、能够复现、能够写进简历的端到端路径。

#### 工程交付

- 一条明确命令或操作路径完成：发现 → 研究 → 编辑决定 → Markdown 输出；
- 准备脱敏演示数据和失败示例；
- 文档说明当前能力、限制和回滚方法；
- 记录真实运行数量、处理时间、引用覆盖和人工采用结果；
- 形成两版简历表述草稿：AI Agent/评测版与后端/工程版。

#### 用户可见效果

项目本人可以每天或每周实际使用；面试时可以用一个真实事件演示系统为何推荐、如何补证、何时阻止发布。

#### 验收标准

- 在干净环境或明确环境说明下可复现；
- 真实跑通至少 3 次，失败可见；
- 至少处理 5 个真实事件，生成 2 份合格 Dossier；
- 至少 1 份被采用为稿件或发布；
- 演示不读取或暴露密钥、Cookie、邮箱和私人数据；
- 所有简历数字来自真实记录，不使用占位数据。

#### 本阶段不做

不为了简历硬塞 K8s、Spark、微调或多 Agent；没有实际使用和测量就不写。

#### 停止条件

如果截止日前完整范围不能通过验收，缩小为一条证据完整的真实纵切并如实展示限制；
不能为了日期伪造指标、删除失败样例或把计划能力写成已实现。

---

### Stage E：日常交付与反馈闭环

**状态：`planned`，8 月底以后**

#### 用户可见效果

- 每天 0～3 张 Signal Card；
- 每周 2 份 Dossier；
- 每周至少 1 份被采用或发布；
- 用户的采用、拒绝和修改原因能进入下一周复盘。

#### 验收标准

- 连续运行 2 周；
- 不重复推送没有实质变化的事件；
- 所有用户反馈可追溯且不会丢失；
- 周报只描述观察，不用小样本宣称已经学会用户偏好。

#### 停止条件

如果连续一周没有任何 Dossier 被采用，先修选题、证据或表达，不增加新数据源。

---

### Stage F：按缺口接入 Reddit 与 X

**状态：`planned`，Later**

#### 顺序

- 当 Dossier 经常缺少真实使用与失败案例时，先接 Reddit；
- 当系统经常错过首发、作者解释与短期扩散时，再接 X；
- 新来源先影子运行，只补证，不直接挤入每日 Top 3。

#### 验收标准

新增来源必须提供 GitHub/Official 没有的关键事实、反例或真实任务；如果主要产生重复与噪声，则保留为按需查询，不进入常规扫描。

#### 停止条件

影子期若新增来源没有提供独特可用证据，或登录/接口恢复成本持续高于内容收益，就停止
自动接入，退回按需人工查询。

---

### Stage G：学习与产品化

**状态：`planned`，Later**

只有积累足够的真实采用、拒绝、编辑差异和发布结果后，才考虑：

- 版本化排序权重与离线回放；
- 影子实验、审批和回滚；
- 多主题、多用户或团队产品；
- 订阅、部署或商业化。

没有稳定反馈样本时，“自动进化”只允许指记录轨迹和调整可解释规则，不得宣称反向训练或在线学习已经有效。

#### 启动与停止条件

只有连续真实使用产生足够的采用、拒绝、编辑差异和发布结果后才允许启动。任何策略
变更若无法通过历史回放、影子运行、人工确认和回滚测试，就不得进入常规流程。

## 4. 8 月底目标排期

| 时间 | 目标 | 可见结果 |
| --- | --- | --- |
| 8 月 15～17 日 | 控制文档初稿与读者测试 | 项目全貌可读、方向冻结 |
| 8 月 18～21 日 | 10～20 个 Golden examples | 好坏标准有真实样例 |
| 8 月 22～26 日 | GitHub + Official/Web Research MVP | 事件级 Dossier |
| 8 月 27～29 日 | Editorial Decision | 能区分可直接写与需亲测 |
| 8 月 30～31 日 | 真实运行、演示和简历证据 | 可用、可演示、可辩护 |

这是目标窗口，不是为了赶日期而降低验收标准。若时间不足，优先保证一条小而真实的端到端路径，后移来源数量与自动化程度。

## 5. 路线图变更规则

任何阶段新增、改序或扩大范围前，必须说明：

1. 它解决哪个已观察到的问题；
2. 用户做完后能得到什么新效果；
3. 为什么现阶段必须做；
4. 用什么证据验收；
5. 为此明确不做什么。

关键变更同时记录到 [05-DECISIONS.md](./05-DECISIONS.md)，当前事实同步到 [02-STATUS.md](./02-STATUS.md)。
