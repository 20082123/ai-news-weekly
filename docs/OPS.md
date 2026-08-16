# OPS — 标准操作手册（代理执行，用户不碰命令行）

> 用户是人，agent 是手。本手册给出七类高频动作的标准命令序列；执行方可以是
> Codex / DeepSeek Harness / WorkBuddy 等任何遵守 [AGENTS.md](../AGENTS.md) 的代理。

```powershell
cd C:\Users\HP\.codex\worktrees\ba2d\ai-news-weekly
$env:PYTHONPATH = "src"
```

数据库：`./.ai-signal/ai_signal.db`；开发输出：`./.ai-signal/Content/`。
生产输出根目录（DEC-018，**已获第一用户批准，2026-08-16**）：
`C:\Users\HP\Documents\AI-Signals\AI Signal\Content\`——批准范围仅限 Vault 的
`AI Signal/` 子树，不得写 Vault 其他位置。

---

## 动作 0：以你的选题为圆心（DEC-017，优先于一切自动化）

```powershell
# 1) 你挑一个想写的（一句话也行），登记：
python -m ai_signal choice pick --db-path ./.ai-signal/ai_signal.db `
  --week-key 2026-W33 --subject "DeepSeek 涨价" [--event-id <关联事件id>]

# 2) 看证据缺口清单（哪些窟窿、找哪个源、怎么补）：
python -m ai_signal choice gaps --db-path ./.ai-signal/ai_signal.db `
  --week-key 2026-W33 --subject "DeepSeek 涨价"

# 3) 按缺口补源（见动作 2/3/4），够了就停；然后研究/判定/出稿（动作 2 的后半）

# 4) 发布后记反馈（adopted / parked / rejected，选题级快速判断）：
python -m ai_signal choice feedback --db-path ./.ai-signal/ai_signal.db `
  --week-key 2026-W33 --subject "DeepSeek 涨价" `
  --decision adopted --reason "数据不错，继续这类" --usefulness 5 `
  [--published-url "https://..."]
```

原则：**圆心是你的选择，不是事件堆**。每周采集（动作 1）只是候选供货，
不是主流程；Reddit/X 只在某个选题的缺口打开时才去（动作 3）。
选题卡级的两轮反馈（读卡判断 + 发布结果）走动作 7。

## 动作 1：本周全流程（发现 → 研究 → 判定 → Brief）

```powershell
python -m ai_signal weekly run --db-path ./.ai-signal/ai_signal.db `
  --week-key 2026-W33 --allow-network `
  --output-root ./.ai-signal --allow-output-write
```

- 一条命令跑完：4 车道 GitHub 发现 → 官方公告采集 → 队列提升 → 研究 top-N →
  editorial 判定 → Brief 文件。
- 结果只有安全计数；`partial`/`failed` 会显式报告并退出码 5，绝不静默。
- 匿名 GitHub 额度限流（`GITHUB_RATE_LIMITED`）时：如实告知用户，一小时内
  自动恢复后重跑即可（游标不受影响，重跑幂等）。

## 动作 2：研究单个事件（从事件到可写 Brief）

```powershell
# 找到事件 id（subject 列）
python -m ai_signal event-candidate list --db-path ./.ai-signal/ai_signal.db

# 读 README/Release 建档案（需要 GitHub 引用 + 联网）
python -m ai_signal research build --db-path ./.ai-signal/ai_signal.db `
  --event-id <event_id> --allow-network

# 官方一手页补证据（2D3；事件还没有档案时自动建档——DEC-018 按需查证）
python -m ai_signal research add-evidence --db-path ./.ai-signal/ai_signal.db `
  --event-id <event_id> --url "https://官方公告页" --allow-network

# 判定（证据变化自动出新修订）
python -m ai_signal editorial decide --db-path ./.ai-signal/ai_signal.db --event-id <event_id>

# 出 Brief 文件（含四问角度草稿）
python -m ai_signal content brief --db-path ./.ai-signal/ai_signal.db `
  --event-id <event_id> --week-key 2026-W33 `
  --output-root ./.ai-signal --allow-output-write

# agent 补 ②③ 草案：把「受众+前后对比数字+标题」写进卡的草稿区
# （系统只出零件和 ①④；②③ 由 agent 按事实拟稿、用户终审——见动作 6）

# 查看档案全文
python -m ai_signal research show --db-path ./.ai-signal/ai_signal.db --event-id <event_id>
```

## 动作 3：录入 Reddit/X 用户现实证据（Phase 4）

```powershell
# 1) 用 agent-reach 采集真实帖子（登录态在 agent-reach 侧）：
opencli reddit search "关键词" -f yaml
opencli twitter search "关键词" -f yaml

# 2) 逐条录入（不联网、原文照录、单条=个案，措辞必须标注"不代表普遍"）：
python -m ai_signal research add-note --db-path ./.ai-signal/ai_signal.db `
  --event-id <event_id> --kind fact|official_claim|contradiction|unknown `
  --text "引用内容+出处（r/xx，N 赞）" --url "https://www.reddit.com/r/..."

# 3) 重新判定并重出 Brief（新修订可审计）
```

## 动作 4：官方公告（DEC-018：官网是词典不是雷达）

- **查证（主路径）**：你提出某方面 → 代理给对应官网页 → 抓回挂进档案：
  `research add-evidence`（见动作 2）。任何官网都行（OpenAI、DeepSeek、剪映…），
  不需要维护 feed 清单。
- **被动备份（可有可无）**：

```powershell
python -m ai_signal official collect --db-path ./.ai-signal/ai_signal.db --allow-network
python -m ai_signal official list --db-path ./.ai-signal/ai_signal.db --limit 20
```

源目录：`src/ai_signal/discovery/official_catalog.py`（草稿，替换即生效）。
RSS 扫描不再是要确认的关键路径，扫到东西算白赚，扫不到不影响主流程。

## 动作 5：查看队列与档案（只读）

```powershell
python -m ai_signal discover github --status --db-path ./.ai-signal/ai_signal.db
python -m ai_signal event-candidate list --db-path ./.ai-signal/ai_signal.db
python -m ai_signal official list --db-path ./.ai-signal/ai_signal.db
```

## 动作 6：出“初稿提示包”（四问翻译 → 喂外部模型）

1. 从 Brief/档案里取事实，按四问翻译：①无术语变化 ②谁的任务变了（人群+任务）
   ③之前 vs 现在（带数字）④现在做什么（去用/等等看/不用管）；
2. 组装提示包：四问答案 + 必须出现的事实（带链接）+ 禁说清单 + 标题候选 +
   初稿要求（结构/语气/长度）；
3. 交给外部模型出初稿（**系统内不接 LLM 写稿**）；交用户终审；
4. 样例：`docs/golden-set/GS-21-ai-agent-book.md`。

## 动作 7：两轮反馈回流（DEC-018）

选题卡（`kind: content-brief`）frontmatter 自带 9 个反馈字段，用户在
Obsidian 里填，代理跑 sync 落库：

```powershell
# Content 目录 = 选题卡所在目录（生产：C:\Users\HP\Documents\AI-Signals\AI Signal\Content）
python -m ai_signal feedback sync --db-path ./.ai-signal/ai_signal.db `
  --content-dir "C:\Users\HP\Documents\AI-Signals\AI Signal\Content" --allow-feedback-write

# 2B 时代的旧 Inbox 包（可选，同时扫）：
python -m ai_signal feedback sync --db-path ./.ai-signal/ai_signal.db `
  --inbox-dir "./.ai-signal/Inbox" --content-dir "./.ai-signal/Content" `
  --allow-feedback-write
```

- 第一轮（读卡后，**可填可不填**）：decision 填中文「采用 / 暂存 / 拒绝」
  （sync 自动翻译成 adopted/parked/rejected），可加 reason/audience/angle/usefulness；
- 第二轮（发布几天后，**必填**）：published_url + published_at(YYYY-MM-DD)
  + outcome（真实表现）+ lesson（一句话复盘）；
- 两轮同落 `feedback` 表；后补第二轮 = 新增一行（旧行不动），判断演变可审计；
- 不填 decision 的文件被跳过（skipped），坏文件跳过计数（invalid），
  StorageError 整体回滚，绝不部分提交；
- 欠账清单（已发布未填结果）用 Obsidian Dataview 查：
  `published_url 有值且 outcome 为空`，零代码。

---

## 收尾清单（每次操作后）

- `git status --short` 确认改动范围；改代码后 `git diff --check` + 全量测试；
- 任何提交必须等第一用户批准（AGENTS.md 硬约束）；
- 向用户交付：自然语言结论 + 文件路径 + 如实标注的机器草案 vs 人工待定项。

## 动作 8：初稿与发布（DEC-019/021：小红书先行、系统出格式、表达外包）

1. **提示包（命令）**：`content prompt-pack --event-id <id> --week-key <周>`
   系统组装护栏包（四问骨架 + 全部事实 + 禁说清单 + 平台规则）写进 Vault
   的 `AI Signal/Drafts/`；agent 填 ②③ 草案；
2. **平台骨架稿（命令）**：`content platform-draft --platform xiaohongshu`
   系统渲染骨架（事实清单/禁说清单/配图清单已放好），agent 或豆包只填
   表达空位；豆包改写版必须原样带禁说清单；
3. **配图模板**：骨架稿自带配图清单（官方页截图 + 红框标注关键数字 +
   配字 ≤12 字），照做即可；
4. **发布**：用户登录平台复制粘贴发布（agent 不代发）；来源链接放评论区；
5. **回流**：发布后用户在选题卡填第二轮反馈，说「同步反馈」，agent 跑
   `feedback sync`；每平台发一次记一次，积累「哪个平台是主场」的数据。
