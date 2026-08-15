---
title: AI Signal Agent 当前状态
owns: 当前分支、已验证事实、正在做什么、下一步和风险
does_not_own: 产品定义、完整阶段规格、长期架构、数据库字段
status: active
last_updated: 2026-08-15
---

# AI Signal Agent 当前状态

这是每次打开项目时首先阅读的页面。它回答：**今天项目究竟在哪里。**

产品目标见 [00-PRODUCT.md](./00-PRODUCT.md)，完整阶段和验收见 [01-ROADMAP.md](./01-ROADMAP.md)。

## 1. 一句话状态

> **线上旧周报继续稳定运行；新 AI Signal 影子分支已完成可靠的 GitHub 工程底座和 2C1 候选资格层，但尚未证明能稳定发现对目标用户有价值的 AI 工作变化。2C2 GitHub Discovery Policy Adapter 已工程完成（离线测试通过）并真实运行一次；Golden Set 以 4 份档案 + 20 行候选表封版（有好有坏）；2C3 source-independent EventCandidate 契约已开始实施。**

### 两套编号怎么理解

- `Phase 1 / 2A / 2B / 2C` 是历史实现编号，用来对应代码提交；
- `Stage A～G` 是从现在开始的产品结果路线图，用来决定下一步；
- 历史上完成 2C1，不代表现在应该继续做 2C2；2026-08-15 第一用户决定重启 2C2，但**严格限定为 GitHub-specific policy adapter**——全局 Signal/Event 层仍等 Golden Set 与 Stage B 规格确认。

### 三句话记住当前全貌

1. V0 邮件周报在线可用，但不是目标产品；
2. 2B 真实跑过但内容失败，只保留工程资产；
3. 2C1 测试通过但未真实运行，当前先定义好坏标准，再决定后续代码。

## 2. 代码快照

| 项目 | 当前事实 |
| --- | --- |
| Worktree 分支 | `codex/ai-signal-foundation` |
| 当前 HEAD | `4b2e9c3 docs: add golden-set workspace with four drafted annotation dossiers` |
| 工作树 | 干净，无未提交文件 |
| 相对本地 `origin/main` 引用 | 领先 13 个提交；本次未联网 fetch，因此不对远端实时状态作额外推断 |
| 全量测试 | 529 项通过，2 项跳过 |
| 线上入口 | 仍为 `python main.py` |
| GitHub Actions | 仍执行旧 V0 周报，不读取 shadow 新管线 |
| 冻结文件 | `main.py`、`requirements.txt`、`.github/workflows/weekly.yml` 相对本地 `origin/main` 无差异 |

## 3. 当前实际存在三条路径

### A. V0 线上邮件周报

**状态：`verified`，继续运行**

```text
Tavily + GitHub Trending
→ LLM 汇总
→ Markdown / HTML
→ 邮件
```

它是当前生产基线，没有被 shadow 分支替换。它能发送周报，但没有事件级研究、编辑门控和反馈闭环。

### B. Shadow 2B 仓库素材原型

**状态：`superseded`，只保留兼容和历史证据**

```text
GitHub Repository
→ Event
→ A–F MaterialPack
→ Markdown Inbox
```

工程上已跑通，真实生成过 10 份中文文件；这些正是被第一用户判断为低质量的素材。

它证明了以下工程能力可复用：

- 确定性 ID、SQLite 事务与幂等；
- Claim–Evidence 绑定；
- Markdown 输出和反馈字段；
- 安全校验与脱敏。

它也证明了以下产品假设错误：

- Repository 不能直接等于 Event；
- 仓库元数据不能直接生成内容素材；
- 中文化和 A–F 模板不会自动提高素材质量。

### C. Shadow 2C Candidate 推荐路径

**状态：2C1 工程 `verified`，真实运行与产品效果均未验证**

```text
GitHub Search Result
→ GitHub Candidate Qualification
→ research / watch / reject
→ Research（尚未实现）
→ Editorial（尚未实现）
```

2C1 已完成 Candidate、Discovery、Assessment 三层身份与可解释 Gate，只回答：

> 这个 GitHub 候选是否值得继续花成本读取 README、Release 等资料？

它不负责判断是否可以发布，也不改善 GitHub Search 的随机性。

本地真实 Vault 尚未迁移并运行 2C1，因此目前没有 2C1 的真实用户可见结果。

## 4. 阶段状态表

| 阶段 | 状态 | 用户现在能得到什么 | 关键缺口 |
| --- | --- | --- | --- |
| V0 邮件周报 | `verified` | 每周邮件 | 不符合新的事件/工作影响目标 |
| Phase 1 基础设施 | `verified` | 无直接内容输出 | 只是地基 |
| 2A fixture 采集 | `verified` | 无真实素材 | 仅离线样本 |
| 2B1 GitHub REST | 工程与单次真实运行 `verified` | 能抓公开仓库元数据 | 召回质量低、没有具体事件 |
| 2B2/2B3 A–F 素材 | 工程 `verified`，产品 `failed` | 10 份低质量中文素材 | repo≠event、缺 README/Release/影响研究 |
| 2C1 Candidate | 工程 `verified` | 暂无真实用户效果 | 未真实运行，只是研究资格 Gate |
| 2C2 GitHub Discovery Policy | 工程 `verified`（离线测试通过）；**已真实运行一次**（2026-W33，13 请求全 success） | 产出研究队列 20 条 queued | 待第一用户清单确认与后续 smoke | 只做 GitHub-specific policy adapter，输出 Research Queue；真实运行未验证 |
| 2C3 Event Candidate 契约 | `in_progress`（2C3-A：Taxonomy + 0006 + CLI） | 无 | 不确认事件、不复用 2B event 表 | 薄的来源无关契约（DEC-014） |
| Research Dossier | `planned` | 无 | 契约和实现均未完成 |
| Official/Web | `planned` | 无 | 尚无 Adapter |
| Editorial Decision | `planned` | 无 | 尚不能区分直接写/需测试 |
| Reddit / X | `planned`，Later | 无 | 第一版不依赖 |
| 学习与产品化 | `planned`，Later | 无 | 真实反馈样本为 0 |

## 5. 真实数据与反馈事实

本地真实运行记录表明：

- GitHub live 搜索曾采集 10 条原始仓库信号；
- 2B 路径生成 10 个 Signal、10 个 Event 和 10 份 Inbox 文件；
- MaterialPack 因 v1/v2 重建留有两批历史记录；
- 人工 Feedback 为 0；
- Publication、Metric 与 Score 反馈为 0；
- 2C1 尚未在这批真实数据上运行。

因此当前不能声称系统已经学会用户偏好、改善了推荐，或形成了内容复利。

## 6. Current / Next / Later

### Current

- 产品 North Star、第一用户、目标受众与 8 月底目标已由第一用户确认；
- 六份项目控制文档初稿已建立；
- 本地 Markdown 链接、代码围栏和 Git diff 空白检查已通过；
- 无上下文的非技术读者与开发者读者测试均已 `PASS`；
- 第一用户已决定重启 2C2 为 GitHub-specific Discovery Policy Adapter（DEC-013）；
- **2C2-A～D 工程完成并真实运行一次**：Policy Catalog、0005 四表、scope/spec
  防火墙、Mature/Emerging 编排（去重、预算、Selection）、Watchlist 直采、
  Ecosystem metadata relation + Gate v3、CLI 与四条 Golden Cases；2026-W33
  四个策略真实采集（13 请求）全 success，产出研究队列 20 条 queued；
- **Golden Set 封版（有好有坏）**：`docs/golden-set/` 4 份档案 + 20 行候选表；
  GS-12 已记第一用户初判（不吸引 → reject）；
- **2C3-A 进行中**：五类 Signal Taxonomy + `event_candidate`/
  `event_candidate_source_ref`（0006）+ CLI；Golden 档案已种子入库；
- **待第一用户**：Watchlist/Ecosystem 清单确认；后续受控 smoke 授权。

Stage A 尚未完成：Golden Set 当前只有说明性种子，尚未完成 10～20 个真实样例的
第一用户标注；`06-STAGE-B-SPEC.md` 也必须等 Golden Set 后才能起草和确认。

### Next

- 2C3-B：EventCandidate 与 GitHub 研究队列的对接（把 queued 候选引用进
  event_candidate）、官方公告候选的最小形态；
- 第一用户确认/替换 Watchlist 与 Ecosystem 草案清单；
- 经第一用户批准后做后续受控 `--allow-network` smoke；
- 由第一用户继续标注 Golden Set（可在 20 行表中继续填写判定）；
- Golden Set 达标后起草 `06-STAGE-B-SPEC.md`，进入 2D1/2D2 自动深研。

### Later

- Editorial Decision 与日常 Markdown 输出；
- 两周真实使用和反馈；
- 按证据缺口接 Reddit，再按时效缺口接 X；
- 有足够反馈后再考虑排序学习、多用户与商业化。

## 7. 当前冻结项

- 2C2 只做 GitHub-specific policy adapter（DEC-013）：不产全局 Signal/Event、不接 Official/X/Reddit、不做 LLM 排序、不做 YAML/DSL policy engine；
- 不新增 X/Reddit Adapter；
- 不修改 V0 `main.py` 和线上 workflow；
- 不继续扩充 A–F 模板；
- 不引入向量库、复杂 Agent 框架、RL 或自动进化；
- 不把测试数量当成内容价值证明。

## 8. 当前主要风险

1. **再次来源中心化**：因为 GitHub 已实现，继续把所有概念设计成仓库结构。
2. **工程完成冒充产品完成**：测试很多，但没有真实采用数据。
3. **阶段文档再次重复**：README、ROADMAP、STATUS 同时定义当前状态。
4. **为赶 8 月底扩大范围**：接更多来源，却没有一条可用的端到端闭环。
5. **过早学习**：Feedback 为 0 时讨论自动进化，只有复杂度没有有效信号。

对应控制办法分别记录在 [00-PRODUCT.md](./00-PRODUCT.md)、[01-ROADMAP.md](./01-ROADMAP.md) 和 [05-DECISIONS.md](./05-DECISIONS.md)。

## 9. 下一项需要第一用户确认的内容

阅读并修订本套文档，然后进入 Golden Set 共创。下一项产品工作不是写新 Adapter，而是共同挑选 10～20 个真实 AI 变化，标出哪些值得写、哪些需要测试、哪些应该拒绝，以及为什么。
