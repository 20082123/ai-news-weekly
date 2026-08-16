---
title: AI Signal Agent 决策记录
owns: 跨阶段关键决定、原因、代价及替代关系
does_not_own: 普通待办、每日进度、完整阶段说明、实现细节
status: active
last_updated: 2026-08-15
---

# AI Signal Agent 决策记录

这份文档回答：**为什么项目要这样做。**

状态：

- `accepted`：已经确认，后续工作必须遵守；
- `proposed`：当前建议，仍需第一用户在真实样例中验证；
- `superseded`：曾采用，但已被新的决定替代；
- `rejected`：明确不采用。

普通代码选择、TODO 和每次提交不进入本文件。

`proposed` 只允许作为 Stage A 的可见工作假设：它可以帮助组织 Golden Set，但不能
单独授权数据库迁移或业务代码。引用 Proposed Decision 的未来阶段均为暂定规划，必须
在首个实施规格前由第一用户确认。

## DEC-001：产品以受众为先、与来源无关

- **日期**：2026-08-15
- **状态**：`accepted`

### 背景

项目从“发现对目标用户有价值的 AI 变化”逐渐偏向“发现 GitHub 上值得研究的 AI 仓库”。GitHub 数据结构开始影响整个产品概念。

### 决定

> 不追所有 AI 新闻，只追会实质改变目标用户工作、创作、决策或花钱方式的 AI 变化，并告诉他现在应该做什么。

### 原因

用户价值来自工作影响与行动判断，不来自信息来源数量或仓库数量。

### 后果与代价

- 未来顶层对象必须围绕 Event、Work Impact 和 Evidence；
- 已完成的 GitHub 能力降为第一个 Sensor；
- 需要 Official/Web 等非 GitHub 证据；
- 短期内会先写文档和 Golden Set，而不是继续增加代码量。

## DEC-002：第一版只服务项目所有者本人

- **日期**：2026-08-15
- **状态**：`accepted`

### 决定

第一版只服务本人；第一批内容受众是需要亲自用 AI 完成工作的年轻知识工作者和独立创作者。多用户产品化暂缓。

### 原因

在没有个人持续使用、采用和反馈数据前，多用户需求、权限、计费和团队界面都会放大未验证假设。

### 后果与代价

- 可以优先使用本地 SQLite、Markdown/Obsidian 和简单 CLI；
- 不为通用 SaaS 提前建设复杂前端与账户系统；
- 需要真实记录本人为何采用或放弃。

## DEC-003：第一版采用宁缺毋滥的产量目标

- **日期**：2026-08-15
- **状态**：`accepted`

### 决定

- 每天最多 3 条高质量 Signal Card，可以为 0；
- 每周形成 2 份 Research Dossier；
- 每周至少 1 份结果可以直接写或明确需要测试；
- 每周至少 1 份被本人采用为稿件或实际发布。

### 原因

过去“搜索 10 条就生成 10 份素材”证明，固定填满会把采集数量误当质量。

### 后果与代价

系统必须支持空结果和明确拒绝；成功指标从抓取量转向采用率、证据质量和节省时间。

## DEC-004：以 2026-08-31 为首个可用演示节点

- **日期**：2026-08-15
- **状态**：`accepted`

### 决定

8 月底前形成一条小而真实的端到端路径：可实际使用、可现场演示、可用真实数字写入秋招简历。

### 原因

项目同时服务自媒体和求职作品集，需要一个近期收敛点，不能无限建设基础设施。

### 后果与代价

范围优先级高于功能数量；Reddit、X、自动进化和多用户产品必须后置。

## DEC-005：在产品文档与 Golden Set 通过前暂停 2C2

- **日期**：2026-08-15
- **状态**：`superseded`（同日被 DEC-013 替代）

### 决定

暂停 2C2 和新的业务功能开发，先建立所有人都能读取的产品、路线图、状态、架构、黄金样例与决策文档。

### 原因

当前代码稳定且 worktree 干净；偏移来自产品契约缺失，不是某个技术故障。继续编码会扩大语义债务。

### 后果与代价

短期提交以文档和样例为主；通过读者测试与第一用户复核后再恢复业务代码。

### 替代

第一用户同日复核后决定：文档与 Golden Set 继续并行推进，同时**重启 2C2，但严格限定为 GitHub-specific Discovery Policy Adapter**（见 DEC-013）。全局 Signal/Event 层仍然要等 Golden Set 与 Stage B 规格确认。

## DEC-006：GitHub 是第一个 Sensor，不是产品分类体系

- **日期**：2026-08-15
- **状态**：`accepted`

### 决定

将 Watchlist、Mature、Emerging、Ecosystem 明确称为 **GitHub Discovery Lanes**。当前 2C1 Candidate 在语义上属于 `GitHubRepositoryCandidate`，不能代表所有来源的 Candidate。

### 原因

仓库、Stars、Release 和 Issue 只覆盖开源技术世界，无法完整发现闭源产品、价格变化、普通用户体验与工作流采用。

### 后果与代价

- 2C1 的身份、审计和 Gate 可保留；
- 未来其他来源拥有自己的 source-specific Candidate；
- 需要一个来源无关的 Canonical Event 层；
- 是否重命名代码类和迁移数据库，等真实样例验证后再决定。

## DEC-007：2B 仓库直接素材化降级为兼容原型

- **日期**：2026-08-15
- **状态**：`accepted`

### 决定

`materialize github` 与 A–F 输出保留用于回归、演示历史和复用工程组件，但不再作为推荐内容路径。

### 原因

真实运行生成的 10 份中文素材被第一用户判断为低质量；根因是 Repository 被直接当作 Event，而不是排版问题。

### 后果与代价

Claim–Evidence、Markdown、feedback 等基础可复用；旧输出不删除，但 README 必须清楚标记其历史地位。

## DEC-008：Official/Web 先于 Reddit/X

- **日期**：2026-08-15
- **状态**：`proposed`

### 决定

第一版 Research MVP 只使用 GitHub + Official/Web；Reddit 用于后续补真实使用与失败案例，X 用于后续补首发、作者观点与扩散。

### 原因

当前最急迫的缺口是稳定形成可直接写的、带一手证据的 AI 变化。Official announcement、Docs、Release、Pricing 能以较低噪声提供该能力。

### 后果与代价

第一版无法完整覆盖社区情绪和早期传播，但能更快验证事件级研究与编辑门控。

## DEC-009：变化类型、证据类型、受众任务与编辑状态正交

- **日期**：2026-08-15
- **状态**：`proposed`

### 决定

第一版不把所有概念塞进一组 Signal 标签，而是分为：

1. 变化类型：Capability / Workflow / Economics / Ecosystem；
2. 证据类型：Official / Technical / Independent / User Reality / Testing；
3. 受影响的人和任务；
4. 编辑状态：ready_to_write / needs_testing / watch / reject。

### 原因

`User Reality` 更像证据视角，而 Capability 等是变化类型。混为同一 taxonomy 会造成数据库和评分语义不清。

### 后果与代价

先用 10～20 个 Golden examples 验证，再考虑 schema；短期需要人工标注，但可避免过早固化错误本体。

## DEC-010：工程完成、真实运行和产品验证分别记录

- **日期**：2026-08-15
- **状态**：`accepted`

### 决定

任何阶段都分别标记 engineering、real run 和 product 三种状态。测试通过不得自动将产品阶段标记为完成。

### 原因

2B3 已证明：代码、测试和中文输出均能正确运行，但用户仍然认为素材不可用。

### 后果与代价

路线图和状态页需要更多证据链接；换来的是不会再用 423 项测试掩盖 0 个采用样本。

## DEC-011：发布事实与体验型结论采用不同证据门槛

- **日期**：2026-08-15
- **状态**：`proposed`

### 决定

- 由官方公告、Docs、Release、Pricing 直接支持的发布事实，可以进入 `ready_to_write`；
- 好不好用、是否提效、是否稳定、是否适合某任务等体验型结论，必须进入 `needs_testing`，直到有本人实测或方法透明的独立证据。

### 原因

不是所有内容都需要亲测，但也不能把官方宣传当作实际体验。

### 后果与代价

系统能稳定产生部分可直接写内容，同时对高风险体验结论设置明确阻断。

## DEC-012：没有真实反馈前不做“自动进化”

- **日期**：2026-08-15
- **状态**：`proposed`

### 决定

在积累持续的采用、拒绝、编辑差异和发布结果前，自我改进只指：记录轨迹、形成 bad cases、版本化规则、离线回放和人工确认。

### 原因

当前真实 Feedback、Publication 和 Metric 都为 0，没有可学习的稳定 reward。

### 后果与代价

暂不引入在线学习、RL 或自动 Prompt 优化；先获得可信样本，降低不可解释退化风险。

## DEC-013：2C2 限定为 GitHub-specific Discovery Policy Adapter

- **日期**：2026-08-15
- **状态**：`accepted`

### 决定

重启 2C2，但它只负责 GitHub-specific 的发现策略链：

```text
GitHub-specific policy（四条 GitHub Discovery Lane）
→ GitHub collection（复用 2A/2B1 采集与 scope_key 游标）
→ GitHub Candidate Qualification（复用 2C1 Gate）
→ Candidate 级去重与研究预算
→ GitHub Research Queue（github_candidate_selection，DB-only）
```

### 原因

GitHub 已实现，是最便宜的下一个纵切；但继续扩展 GitHub 会把概念继续仓库化。因此 2C2 用命名、表名与文档把它钉死在 GitHub-specific 边界内，为 2C3 的 source-independent 契约铺路。

### 后果与代价

- 2C2 输出不是全局 Signal、不是 Event、不产 A–F 素材、不产 Markdown 报告；
- 新增 `github_` 前缀四表：`github_discovery_run`、`github_discovery_probe_run`、`github_discovery_scope_binding`、`github_candidate_selection`（迁移 0005，纯增量）；
- 每条 probe 独占 scope_key，scope↔spec hash 硬绑定：已存在游标的 scope 若新 spec hash 不同，联网前直接拒绝；
- 预算上限：Watchlist 20/5、Mature 50/5、Emerging 100/8、Ecosystem 50/5；超预算只标 `over_budget`，qualification 决定永不降级；
- Watchlist 与 Ecosystem 的清单由第一用户确认后填入（2C2-C）；
- 不做 README/Release 读取、不做 YAML/DSL policy engine、不做 LLM 排序、不做调度器；
- 2C2 完成后停止连续扩展 GitHub，先实施 2C3 source-independent 契约。

## DEC-014：2C3 只建立薄的 source-independent EventCandidate 契约

- **日期**：2026-08-15
- **状态**：`accepted`

### 决定

2C3 交付：五类全局 Signal Type（`capability_change` / `tool_workflow_change` /
`user_reality` / `economics_access` / `ecosystem_market_shift`）+ `event_candidate`
与 `event_candidate_source_ref` 两表（迁移 0006）+ CLI。EventCandidate 只记录
“候选变化”，**不确认事件**、**不复用 2B legacy `event` 表**、不产素材。

### 原因

在 2C1/2C2 的 GitHub-specific 候选层之上，需要一个最薄的汇合点：同一个真实变化
被多个来源发现时落在同一行。先完成契约，再谈 Gate 与研究。

### 后果与代价

- 源引用用 `(source_kind, ref_id, ref_label)` 指针，不设跨来源外键（官方候选表尚不存在）；
- 首批 Golden Set 档案已作为 EventCandidate 种子写入开发库；
- 事件确认 Gate（confirmed/unresolved/discarded）与 Editorial 留给 2E。

## DEC-016：官方公告是下一个传感器；Reddit/X 证据经手工引文入口进档案

- **日期**：2026-08-15
- **状态**：`accepted`

### 决定

1. 2D3-B 落地 Official 传感器：官方 RSS/Atom 目录（`official_catalog.py`，草稿源）
   采集一手公告候选入 `official_announcement_candidate`（0008），可引用进
   EventCandidate 与研究档案；
2. Phase 4 的 Reddit/X 不建独立抓取器：系统提供 `research add-note` 手工引文
   入口（原文照录 + 逐条 URL + kind），真实帖子由助手经 agent-reach 后端采集后
   录入；登录态只在 agent-reach 侧，永不进入本系统；
3. 依据：战略复盘确认受众关心的消费级变化第一现场是官方公告；用户现实证据
   是小样本高价值信号，按需采集即可，不做提前投资。

### 后果与代价

- `official` CLI（collect/list）与 `weekly run` 增加官方采集步骤（失败可见降级）；
- Reddit/X 的实时可用性依赖 agent-reach 后端（Chrome + OpenCLI 扩展或
  twitter-cli/rdt-cli 登录态）。

> **更新（DEC-018，2026-08-16）**：官方源的角色细化为「词典不是雷达」——
> 发现靠第一用户本人 + GitHub 辅助，`official collect` 降级为被动备份，
> feed 清单不再是要确认的关键路径；手工引文入口（add-note）不变。

## DEC-017：系统的圆心是“你这周写什么”，不是事件堆

- **日期**：2026-08-15
- **状态**：`accepted`

### 决定

1. 新形状（取代“攒事件库”）：
   `你挑选题 → 系统按证据缺口拉来源 → 研究/判定/四问 → 初稿 → 发布 → 反馈回流`；
2. 来源永不排队、无主从：官方公告/GitHub/Reddit/X 围绕**同一个缺口清单**
   各补一格（缺口→来源路由目录 `discovery/gap_routing.py`），够了就停；
3. 选题登记入 `creator_choice`（0009）；发布反馈复用 `feedback` 表
   （target_type=creator_choice，adopted/parked/rejected）；
4. 每周后台采集降级为“候选供货”，不再是主流程。

### 原因

线性流水线会把来源变成上下游、把“先采集后使用”请回来（头号风险“再次
来源中心化”）；以创作者选题为圆心，采集与研究回归中间件本位。

### 后果与代价

- 新增 `choice pick/gaps/feedback/list` CLI；`personal_testing` 缺口永远
  指向第一用户本人；
- 事件库仍保留（供候选与聚合），但不再驱动生产节奏。

## 待第一用户复核的 Proposed Decisions

完成 Golden Set 时，应一起确认或修改：

- DEC-008：GitHub + Official/Web 的第一版来源顺序；
- DEC-009：四条正交分类轴；
- DEC-011：发布事实与体验结论的证据门槛；
- DEC-012：自动进化的启动条件。

一旦确认，把状态改为 `accepted`，并在条目中记录确认日期。若被新决定替代，标记 `superseded` 并链接新条目。

## DEC-018：官网是词典不是雷达；反馈分两轮；输出落进 Obsidian

- **日期**：2026-08-16
- **状态**：`accepted`

### 决定

1. 官方源的定位从「发现雷达」改为「按需查证词典」：发现靠第一用户本人
   （+GitHub 四车道辅助），需要权威数字时由代理对具体方面打官网
   （`research add-evidence --url <官方页> --kind official_claim`）；
   `official collect`（RSS 扫描）降级为可有可无的被动备份，feed 清单
   不再需要维护确认；缺口路由 official_confirmation 措辞同步更新。
2. 人工反馈分两轮：第一轮选题判断（decision/reason/audience/angle/
   usefulness，读卡后判断，可填可不填）；第二轮发布结果（published_url/
   published_at/outcome/lesson，发布几天后必填）。两轮都落在 feedback
   表（0010 新增三列），确定性 ID 覆盖全部已填字段——后补第二轮生成
   新行而不是改旧行，判断演变可审计。
3. 反馈通道接通选题卡：`feedback sync --content-dir` 扫描 Content
   目录的 content-brief 文件（brief_id = 确定性(周+事件)），修掉 2B 时代
   「sync 只认 material_pack」的断头路。
4. 生产输出根目录指向 Obsidian Vault（`AI Signal/Content/`），编辑、
   填反馈、欠账清单（Dataview）都在 Obsidian 完成；系统零依赖。
5. 飞书/豆包暂缓：飞书是以后的分发放大器，豆包是以后的初稿改写工具，
   都要等发布闭环先跑起来再评估。

### 原因

「官网按需查证」对准差异化（数字全对）而非先知道（发现本来就是人的手艺）；
两轮反馈分别校准选题框架与真实受众，第一轮便宜量大、第二轮贵而决定性；
Obsidian 是 Markdown+YAML 的母语环境，接入成本为零。

### 后果与代价

- feedback 表 +3 列（0010），sync 支持双目标类型与双目录；
- 选题卡 frontmatter 增加 9 个反馈字段与 brief_id；
- 真实 Vault 的首次写入仍需第一用户批准（AGENTS.md 硬约束）。

## DEC-019：发布路径——小红书先行、初稿并存、复制粘贴起步

- **日期**：2026-08-16
- **状态**：`accepted`

### 决定

1. **第一个发布平台：小红书**（其余平台模板后续按需扩展，同一事实底座）；
2. **初稿两条并存**：agent 先按提示包写底稿，用户可再拿给豆包做营销改写
   （改写提示词必须原样带禁说清单）；系统内仍不接 LLM；
3. **发布用复制粘贴起步**（用户登录平台贴成品稿）；中期可评估 OpenCLI
   半自动（登录态只在 agent-reach 侧）；**永不**把平台 API token 存进系统
   （凭据永不落库铁律）；
4. 发布后在选题卡填第二轮反馈（published_url / published_at / outcome /
   lesson），`feedback sync` 落库。

### 原因

先完成后完善：先让「成品稿 → 发布 → 反馈」闭环在零新基建下跑通，
再决定是否给某平台做自动化；平台适配是形状问题（同一事实底座），
不是新的事实来源。

## DEC-020：V1 里程碑——首条内容端到端发布

- **日期**：2026-08-16
- **状态**：`accepted`

### 决定

1. 当前分支状态命名为 **V1（首个端到端发布版）**，依据项目自己的三完成纪律：
   工程完成（619 测试全绿）、真实运行（多轮：发现/研究/判定/出卡/发布）、
   产品验证启动（第一条小红书已发布，两轮反馈共 3 行落库，其中一条含
   published_at/outcome/lesson）；
2. **不动 main**：V0 周报（`main.py` + 线上 workflow）继续在 main 运行，
   与 V1 影子分支互不干扰——这正是双轨制（DEC-007 系）的设计：V0 是回退
   基线，V1 旁路演进；本分支只本地提交、本地打标签，不合并、不推送；
3. V1 的完成标准不是「功能更多」，而是「发布→反馈→校准」循环真的转过
   一轮——现已转过第一轮。

### 后果与代价

- 本地标签 `v1.0.0` 打在分支 HEAD 上（纯本地里程碑标记，随时可删）；
- V1 之后的优先级：把「表达 + 配图」这两个真实短板变成产品功能
  （平台稿的表达润色流程 + 配图模板），而不是继续加来源或加自动化。
