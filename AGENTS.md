# AGENTS.md — AI Signal Agent 仓库约定（任何 agent 必读）

任何 Codex / DeepSeek Harness / WorkBuddy / ZCode 等代理在本仓库工作时，先读本文件，
再按 docs/ 下文档操作。你在这里做的是**运营一个内容发现与研究工作流**，不是普通
代码修改。

## 先读什么

1. [docs/02-STATUS.md](docs/02-STATUS.md) —— 今天实际做到哪里；
2. [docs/00-PRODUCT.md](docs/00-PRODUCT.md) —— North Star 与受众；
3. [docs/OPS.md](docs/OPS.md) —— **标准操作手册（高频动作的命令序列）**；
4. [docs/01-ROADMAP.md](docs/01-ROADMAP.md) 与 [docs/05-DECISIONS.md](docs/05-DECISIONS.md) —— 阶段与决策；
5. [docs/06-爆款写作规范.md](docs/06-爆款写作规范.md) —— **替用户写任何平台内容前必读**
   （五要素 + 发稿自检清单，DEC-025 模仿期起生效）；
6. [docs/architecture.md](docs/architecture.md) / [docs/data-model.md](docs/data-model.md) —— 实现细节。

## 硬约束（违反即失败）

- **禁止修改**：`main.py`、`requirements.txt`、`.github/workflows/weekly.yml`（V0 生产路径冻结）；
- **迁移 0001–0008 不可变**；只能新增 0009+（纯增量）；
- 凭据永不落库/落码/落日志；raw query 只存 hash（`query_sha256`/`spec_hash`）；
- 联网必须显式 `--allow-network`；写文件必须 `--allow-output-write`（缺省建库/建文件前拒绝，退出码 4）；
- **不执行 git add/commit/push/merge/rebase/reset/checkout，除非第一用户明确批准**；
- 真实 Obsidian Vault（`C:\Users\HP\Documents\AI-Signals`）**已获批准写入范围
  （2026-08-16）仅限 `AI Signal/` 子树**；Vault 其他位置仍不得写入；
- Reddit/X 登录态只存在于 agent-reach（OpenCLI/twitter-cli/rdt-cli），系统内不存任何登录态；
- 不在系统里接 LLM 写稿：机器出事实/零件/判定草案，初稿由外部模型按提示包生成、人终审；
- **任何 agent 替用户写平台内容（主线/副线成品稿），必须全程遵守
  `docs/06-爆款写作规范.md`，且最后必须过 humanizer 去 AI 味**
  （防平台 AI 检测限流）；没过 humanizer 的稿子不得以「成品」交付用户；
- **平台红线零触发**：成品稿必须逐条过 `docs/06` 的**广告法六条（最高优先级，
  国家法律：绝对化用语/真实不误导/引证有出处/不贬低同行/不承诺收益/不碰医疗）**
  与平台红线八条（AI 生成标识/零引流/不制造对立/不侵权/不诱导互动等）；
  触发任何一条 = 不发；缺出处的数字不写。

## 环境与命令约定

- `$env:PYTHONPATH = "src"`，Python ≥3.10，零第三方依赖；
- CLI 退出码：`0` 成功 / `2` 配置错误 / `3` 数据库错误 / `4` 安全阻断 / `5` partial 或 failed；
- 开发库：`./.ai-signal/ai_signal.db`（git-ignored）；内容输出：`./.ai-signal/Content/`；
- 离线自检：`python -B -m unittest discover -s tests`（应全绿，skipped=2 为 Windows symlink 权限）；
- 每次改代码后跑 `git diff --check`。

## 词汇三套（永不混用）

- qualification：`research / watch / reject`
- editorial：`ready_to_write / needs_testing / watch / reject`
- 人工反馈：`adopted / parked / rejected`（用户在卡上填中文「采用 / 暂存 / 拒绝」，
  sync 边界自动翻译，库里永远是英文）

## 与第一用户的交互

- 用户是普通内容创作者，**不碰命令行**：代理负责执行，向用户交付自然语言结论 +
  文件路径；
- 涉及“写什么/发在哪/是否亲测”的判断，给出机器草案后**必须交用户决定**；
- 所有产出遵守四问翻译与禁说清单（见 `docs/golden-set/`）。
