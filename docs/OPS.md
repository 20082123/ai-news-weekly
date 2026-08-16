# OPS — 标准操作手册（代理执行，用户不碰命令行）

> 用户是人，agent 是手。本手册给出六类高频动作的标准命令序列；执行方可以是
> Codex / DeepSeek Harness / WorkBuddy 等任何遵守 [AGENTS.md](../AGENTS.md) 的代理。

```powershell
cd C:\Users\HP\.codex\worktrees\ba2d\ai-news-weekly
$env:PYTHONPATH = "src"
```

数据库：`./.ai-signal/ai_signal.db`；内容输出：`./.ai-signal/Content/`。

---

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

# 官方一手页补证据（2D3）
python -m ai_signal research add-evidence --db-path ./.ai-signal/ai_signal.db `
  --event-id <event_id> --url "https://官方公告页" --allow-network

# 判定（证据变化自动出新修订）
python -m ai_signal editorial decide --db-path ./.ai-signal/ai_signal.db --event-id <event_id>

# 出 Brief 文件（含四问角度草稿）
python -m ai_signal content brief --db-path ./.ai-signal/ai_signal.db `
  --event-id <event_id> --week-key 2026-W33 `
  --output-root ./.ai-signal --allow-output-write

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

## 动作 4：官方公告采集（2D3-B）

```powershell
python -m ai_signal official collect --db-path ./.ai-signal/ai_signal.db --allow-network
python -m ai_signal official list --db-path ./.ai-signal/ai_signal.db --limit 20
```

源目录：`src/ai_signal/discovery/official_catalog.py`（草稿，替换即生效）。

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

---

## 收尾清单（每次操作后）

- `git status --short` 确认改动范围；改代码后 `git diff --check` + 全量测试；
- 任何提交必须等第一用户批准（AGENTS.md 硬约束）；
- 向用户交付：自然语言结论 + 文件路径 + 如实标注的机器草案 vs 人工待定项。
