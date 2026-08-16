---
title: AI Signal Agent 当前状态
owns: 当前分支、已验证事实、正在做什么、下一步和风险
does_not_own: 产品定义、完整阶段规格、长期架构、数据库字段
status: active
last_updated: 2026-08-16
---

# AI Signal Agent 当前状态

这是每次打开项目时首先阅读的页面。它回答：**今天项目究竟在哪里。**

产品目标见 [00-PRODUCT.md](./00-PRODUCT.md)，完整阶段和验收见 [01-ROADMAP.md](./01-ROADMAP.md)，
决策记录见 [05-DECISIONS.md](./05-DECISIONS.md)，标准操作见 [OPS.md](./OPS.md)。

## 1. 一句话状态

> **V1 达成（本地标签 v1.0.0，DEC-020）：第一条内容已在小红书端到端发布，
> 两轮反馈闭环转过第一轮——读卡判断（拒绝 1 条）+ 发布结果（outcome/lesson）
> 均已落库。机器侧的管线完整且可离线验证（619 测试），真正的短板转向了人侧
> 的创作支持：表达润色与配图。V0 周报继续在 main 运行，与本分支互不干扰。**

### 三句话记住当前全貌

1. 工程管线完整：GitHub 四车道候选 → 事件候选 → 研究档案 → editorial 判定 →
   选题卡（含四问草稿与禁说清单），全程确定性、幂等、失败可见；
2. 来源是传感器不是分类法：官方=按需查证词典（DEC-018），Reddit/X 经手工
   引文入口按需采集（DEC-016），全部围绕同一张证据缺口清单（DEC-017）；
3. 资产闭环已转过第一轮：发布 → 反馈回流 → 校准（V1 之后的迭代方向由
   真实短板决定：表达 + 配图）。

## 2. 代码快照

| 项目 | 当前事实 |
| --- | --- |
| Worktree 分支 | `codex/ai-signal-foundation` |
| 当前 HEAD | 最新一条 `git log -1 --format=%h`（本次文档更新与代码同批提交） |
| 全量测试 | 602 项通过，2 项跳过（Windows symlink 权限，预期） |
| 迁移 | 0001–0010 全部已应用；0001–0008 不可变，0009/0010 纯增量 |
| 线上入口 | 仍为 `python main.py`（V0 冻结，不在本分支开发） |
| 冻结文件 | `main.py`、`requirements.txt`、`.github/workflows/weekly.yml` 无差异 |

开发库：`./.ai-signal/ai_signal.db`（git-ignored）；生产输出（**已批准 2026-08-16**）：
`C:\Users\HP\Documents\AI-Signals\AI Signal\Content\`（批准范围仅限 `AI Signal/` 子树）。

## 3. 真实数据快照（2026-08-16）

| 实体 | 数量 | 说明 |
| --- | --- | --- |
| github_discovery_run | 9 | 四车道策略真实采集（含一次全量限流，失败可见） |
| github_candidate_selection | 351 / 排队 38 | 候选评估与队列 |
| event_candidate | 38 | 含 DeepSeek V4 API(90)、Gemini 3.7 Flash(70)、ChatGPT Business(65) |
| research_dossier | 12 | 档案（事实 40+：官方页/GitHub/manual） |
| editorial_decision | 15 | 可直接写 3 事件（qwen-code/DeepSeek/Gemini）、需亲测 3、观察 6 |
| official_announcement_candidate | 50 | 一手公告（被动备份，非关键路径） |
| 选题卡（Vault Content/） | 5 | qwen-code + DeepSeek + Gemini + ChatGPT Business + ai-agent-book |
| 发布 | 1 | 小红书第一条（DeepSeek 峰谷计价）；复盘：受众错位（②是 API 开发者，非本账号人群）→ 判定改为拒绝，保留为核验型内容样本 |
| creator_choice | 1 | 2026-W33 “DeepSeek 涨价”（**published**，闭环转过第一轮） |
| feedback | 4 | qwen-code 拒绝；DeepSeek 采用+outcome/lesson → 复盘拒绝（受众错位）；中枢 adopted |

Golden Set：4+1 份档案（GS-01/06/07/12/21）+ 20 行候选标注表，
仍待第一用户继续填写判定。

## 4. 阶段状态表（只列有意义的状态）

| 阶段 | 状态 | 用户能得到什么 |
| --- | --- | --- |
| V0 邮件周报 | `verified`，继续运行 | 每周邮件（旧产品线） |
| 2C2 GitHub Discovery Policy | `verified` + 真实运行 | 四车道候选 → 研究队列 |
| 2C3 EventCandidate 契约 | `verified` + 真实运行 | 来源无关的事件候选 |
| 2D1/2D2 研究档案 | `verified` + 真实运行 | dossier + 事实（README/Release） |
| 2D3-B Official 传感器 | `verified`，降级为被动备份（DEC-018） | 一手公告候选 |
| 2E Editorial 判定 | `verified` + 真实运行 | ready_to_write / needs_testing / watch |
| 2F 选题卡 | `verified` + 真实运行 | Markdown 选题卡（四问草稿+禁说清单） |
| Phase 3 weekly run | `verified` + 真实运行 | 一条命令端到端 |
| DEC-017 创作者中枢 | `verified` + 真实运行 | choice pick → gaps → 按缺口补源 |
| DEC-018 两轮反馈 + Obsidian | `verified`（通道） | sync 落库；等第一条内容走通 |
| Reddit / X 证据 | 手工引文入口 `verified` | add-note 已录 2 条真实 Reddit 事实 |
| 发布 | `pending` | 平台未定，第一条内容未发 |
| 学习与产品化 | `planned`，Later | 真实反馈样本为 0 |

## 5. Current / Next / Later

### Current（已完成，本轮）

- DEC-017：`creator_choice`（0009）+ 证据缺口路由 `gap_routing.py` + `choice`
  CLI（pick/gaps/feedback/list）；真实演示：DeepSeek 涨价已 pick，五类缺口
  清单已生成；
- DEC-018：反馈两轮化（0010 三列）+ `feedback sync --content-dir` 接通选题卡
  （修掉“sync 只认 material_pack”的断头路）+ 官网定性为词典 + 输出根目录
  指向 Obsidian；
- 修复 `choice list` 无 week-key 崩溃（回归测试 2 个）；
- DEC-019：发布路径（小红书先行 / 并存 / 复制粘贴）+ 第一条已发布，
  两轮反馈共 3 行落库，选题中枢状态 published；
- DEC-020：V1 里程碑（本地+远端标签 `v1.0.0`，分支已推送 origin，
  main 与 V0 周报零改动）；
- DEC-021：平台发布模板（`content prompt-pack` / `content platform-draft`，
  小红书注册表 + 骨架稿 + 配图清单），625 测试全绿。

### Next

- **真正的第一篇（受众匹配已过）**：ai-agent-book 免费中文书——②直接落在
  「想学 AI Agent 的学生」，骨架稿+提示包已生成并填好（Drafts/），等你
  终审后发布；
- 几天后回填 DeepSeek 第一条的平台数据（阅读/点赞/收藏），补进 outcome 再同步一次；
- free-coding-models：等匿名限流窗口复位后重跑 discovery/research 补管道；
- Golden Set 继续标注。

### Later

- 飞书（分发放大器）、豆包（初稿/营销改写）——发布闭环跑起来后再评估；
- 排序学习与多用户——有足够真实反馈之后。

## 6. 当前主要风险

1. **再次来源中心化**：把已实现的 GitHub 结构套到所有概念上。控制：缺口
   路由以选题为圆心，来源只按缺口召唤。
2. **工程完成冒充产品完成**：测试 625 项，发布 1 条、反馈 4 行——产品验证
   刚开始，控制：每篇发布前过「受众匹配自查」（尝菜），不再加机器功能。
3. **发布拖过 8 月底**：管线已经完整，价值只剩“发出第一条”。控制：把
   发布动作排到一切优化之前（先完成后完善）。
4. **营销改写冲垮事实纪律**：豆包等工具最擅长加最高级和没出处的数字。
   控制：提示词必须原样带禁说清单，终稿前对一遍数字。

对应控制办法见 [00-PRODUCT.md](./00-PRODUCT.md) 与 [05-DECISIONS.md](./05-DECISIONS.md)。

## 7. 下一项需要第一用户确认的内容

1. 第一条内容发在哪个平台（X / 公众号 / 小红书 / B站 / 其他）；
2. ai-agent-book 选题是否作为第一条发出（初稿已就绪）；
3. ~~批准真实 Obsidian Vault 写入路径~~ **已批准（2026-08-16）**：
   `C:\Users\HP\Documents\AI-Signals\AI Signal\Content\`，第一张卡
   （QwenLM-qwen-code）已写入。
