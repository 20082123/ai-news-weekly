# AI News Weekly

一个用于自动生成并发送 AI 周报的 Python 项目。脚本会抓取近期 AI 新闻和 GitHub Trending 项目，调用大模型生成中文周报，并通过邮件发送。

## 功能

- 通过 Tavily 获取近一周 AI 相关新闻。
- 抓取 GitHub Trending，并补充仓库 stars、forks、topics、README 摘要等信息。
- 支持多模型容错生成周报：智谱 GLM、Gemini、DeepSeek。
- 将 Markdown 周报转换为 HTML 邮件并发送。
- 支持 GitHub Actions 定时运行，也支持本地测试模式。

## 运行方式

### GitHub Actions 运行

本项目默认通过 GitHub Actions 定时运行，工作流文件位于 `.github/workflows/weekly.yml`。

触发方式：

- 每周一 00:00 UTC 自动运行，即北京时间每周一 08:00。
- 支持在 GitHub Actions 页面手动触发 `workflow_dispatch`。

需要在 GitHub 仓库的 `Settings -> Secrets and variables -> Actions` 中配置以下 Secrets：

- `TAVILY_API_KEY`
- `GEMINI_API_KEY`
- `DEEPSEEK_API_KEY`
- `ZHIPU_API_KEY`
- `EMAIL_USER`
- `EMAIL_PASS`
- `EMAIL_TO`

其中大模型 Key 至少配置一个即可。脚本会按以下顺序尝试调用：

1. 智谱 GLM
2. Gemini
3. DeepSeek

### 本地运行

安装依赖：

```bash
pip install -r requirements.txt
```

复制环境变量模板：

```bash
cp .env.example .env
```

然后在 `.env` 或当前 shell 环境中配置所需变量，再运行：

```bash
python main.py
```

如果只想验证流程，不调用真实 Tavily 搜索和 GitHub Trending 抓取，可以设置：

```bash
TEST_MODE=true
```

## 关于 `.env.example`

项目实际部署使用 GitHub Actions Secrets，不会把真实 Key 写入仓库。

`.env.example` 的作用是给本地运行和二次开发提供变量清单，文件中只有变量名，没有任何真实密钥。公开仓库保留它是合理的，也方便其他人复现项目。

## 证明材料

仓库中包含两份已脱敏的证明材料：

- [具体内容_已标记密文.pdf](./具体内容_已标记密文.pdf)
- [邮件证明.jpg](./邮件证明.jpg)

请在公开仓库前再次确认 PDF 和 JPG 中的账号、邮箱、Key、收件人、内部链接等敏感信息已经完成脱敏。

## 开源前注意事项

- 当前代码通过环境变量读取密钥，源码中不应提交真实 API Key 或邮箱授权码。
- GitHub Actions 使用 Secrets 注入运行时变量，公开仓库后不要在 issue、PR、日志或 README 中粘贴真实密钥。
- 建议在公开前补充 `LICENSE` 文件，明确开源协议。
- 如果不希望公开 Git 提交作者邮箱，需要在转为 public 前重写 Git 历史作者信息。

## AI Signal Agent 影子重构

仓库中新增了 `src/ai_signal/` 包，作为周报流程的“影子重构”基础。
目前它只是离线基础设施，**尚未切换 `main.py`，也尚未修改 GitHub Actions 的
weekly 工作流**，生产线上仍然由 `python main.py` 负责。

这一阶段只交付：标准 Python 包结构、领域模型、状态机、来源契约、本地
SQLite v1 与迁移、离线 CLI，以及带脱敏的结构化日志。它**不**实现真实采集、
LLM 调用、素材包生成、Obsidian 发布或邮件切换。

> 说明：该包默认以 `shadow` 模式运行，网络、邮件与发布能力一律关闭。
> 只有在显式切换到 `live` 模式后才会开启，而这一阶段并不做此切换。

第一阶段离线 CLI 已实现。由于仓库采用标准 `src` layout 且本阶段不安装包，
先在当前 PowerShell 会话设置模块搜索路径：

```powershell
$env:PYTHONPATH = "src"

# 查看配置状态（只输出 configured / not configured，不输出具体值）
python -m ai_signal config check

# 初始化本地 SQLite 数据库
python -m ai_signal db init --path ./.ai-signal/ai_signal.db

# 查看 schema 状态
python -m ai_signal db status --path ./.ai-signal/ai_signal.db

# 离线环境自检（不联网、不读 Cookie、不创建 Vault、不发邮件）
python -m ai_signal doctor
```

相关文档见 [docs/architecture.md](./docs/architecture.md) 与
[docs/data-model.md](./docs/data-model.md)。该重构是纯增量改动，删除
`src/ai_signal/`、`tests/`、`docs/`、`pyproject.toml` 即可完整回滚，不会
影响现有 legacy 流程。

### 第二阶段 2A：GitHub 离线增量采集（fixture 模式）

第二阶段 2A 在影子包里加入了一条**离线**的 GitHub 增量采集路径：读取本地
JSON fixture，经过 `GitHubSource` 解析为 `SourceBatch`，再由 pipeline 写入
本地 SQLite（`source_run` / `raw_signal` / `source_cursor`）。

明确说明：

- **GitHub 网络采集尚未启用**。2A 只支持 fixture，不存在 online/real/live 网络
  模式，也不调用 Agent-Reach。
- **GitHub Actions 与 `main.py` 仍未切换**，生产仍由 `python main.py` 负责。
- 该路径默认 `shadow`，cursor 仅在某次采集 `success` 且返回了与当前值不同的
  非空 next cursor 时才推进；末页、重复 cursor、`partial` / `unavailable` /
  `failed` 都不推进或清空已有 cursor。
- 采集进度按 `(source, scope_key)` 隔离。`--scope-key` 是稳定的逻辑采集范围别名
  （如 `github-fixture-v1`、`ai-agents-v1`），**不是**原始 GitHub query、URL、路径
  或日期；它必须匹配 `^[a-z0-9][a-z0-9._-]{0,63}$`。原始 query 永不进入 cursor 表。
  当查询语义发生不兼容变化时，应升级版本化 scope（如 `ai-agents-v1` →
  `ai-agents-v2`），不同 scope 的 cursor 互不覆盖。

离线 fixture 示例（仓库采用 `src` layout，本阶段不安装包，需设置模块路径）：

```bash
# Bash / Git Bash
PYTHONPATH=src python -m ai_signal collect github \
  --fixture tests/fixtures/github/pages.json \
  --db-path ./.ai-signal/ai_signal.db \
  --week-key 2026-W33 \
  --scope-key github-fixture-v1
```

```powershell
# PowerShell
$env:PYTHONPATH = "src"
python -m ai_signal collect github `
  --fixture tests/fixtures/github/pages.json `
  --db-path ./.ai-signal/ai_signal.db `
  --week-key 2026-W33 `
  --scope-key github-fixture-v1
```

输出只包含 run id、状态、处理数量、warning 数和 cursor 是否推进，不会打印
fixture / 数据库的绝对路径、项目 URL 或 payload。数据库路径示例统一使用
`.ai-signal`，不写入任何用户真实路径。

> 退出码：成功 `0`，配置错误（如非法 week-key）`2`，数据库错误 `3`，安全策略
> 阻断 `4`，`partial` / `unavailable` / `failed` 等非成功完成 `5`。

### 第二阶段 2B1：真实 GitHub 公共数据只读采集（可选联网）

2B1 在影子包里新增一条**受控的、只读的**真实 GitHub 公共仓库搜索路径
（`collect github-live`），复用 2A 的 `GitHubSource` / `collect_source_once` /
`source_run` / `raw_signal` / `source_cursor`。

明确说明：

- **默认不会联网**。必须显式提供 `--allow-network` 才会发起真实请求；否则在创建
  数据库和发起请求之前就阻断（退出码 `4`）。fixture 路径（`collect github`）永远
  不触发真实网络。
- **本阶段不使用 token**。只采集公共数据，使用匿名 GitHub API 限额；不发送
  `Authorization` / `Cookie` 或任何环境数据。
- **只访问** `https://api.github.com/search/repositories`；禁止跟随到其它主机的
  跳转，响应最终 URL 会被重新校验。
- 每次请求最多 `25` 条、单个 scope 最多 `3` 页。
- **scope_key 继续隔离查询进度**；原始 GitHub query 只用于构造 HTTPS 请求，**绝不**
  进入 `scope_key`、`source_cursor`、`config_snapshot`、日志、CLI 输出、warning 或
  异常消息——`config_snapshot` 只保存 `query_sha256` 与安全参数。
- `main.py` 和 GitHub Actions **仍未切换**，生产仍由 `python main.py` 负责。真实
  GitHub smoke 只能由人工手动执行。

示例（仓库采用 `src` layout，本阶段不安装包）：

```bash
# Bash / Git Bash
PYTHONPATH=src python -m ai_signal collect github-live \
  --query "topic:ai-agent pushed:>2026-08-01" \
  --scope-key ai-agents-v1 \
  --db-path ./.ai-signal/ai_signal.db \
  --week-key 2026-W33 \
  --per-page 10 \
  --max-pages 1 \
  --allow-network
```

```powershell
# PowerShell
$env:PYTHONPATH = "src"
python -m ai_signal collect github-live `
  --query "topic:ai-agent pushed:>2026-08-01" `
  --scope-key ai-agents-v1 `
  --db-path ./.ai-signal/ai_signal.db `
  --week-key 2026-W33 `
  --per-page 10 `
  --max-pages 1 `
  --allow-network
```

CLI 输出仍只包含 run id、状态、处理数量、warning 数和 cursor 是否推进；不会打印
query、query_sha256、scope_key、请求/项目 URL、数据库路径、header、响应正文、
payload 或异常原文。

### 第二阶段 2B2：确定性素材闭环（离线，不使用 LLM）

2B2 在已采集 `raw_signal` 的基础上，构建一条纯确定性的素材生产闭环：

```
raw_signal → 规范化/去重 → 信号卡 → Event → Claim-Evidence
→ A–F 多角度素材包 → Markdown Inbox → 人工编辑 frontmatter → feedback sync 写入 SQLite
```

明确说明：

- **本阶段不使用 LLM，不访问网络，不写真实 Obsidian Vault**。
- 所有事实 Claim 仅从 GitHub API 单次快照的已有字段确定性生成（full_name、URL、
  pushed/updated_at、stars、forks、language、topics），不得生成“正在爆火”等无法
  从单次快照证明的结论。
- 每个 Claim 至少绑定一个 Evidence，数字/日期/URL 必须可追溯到绑定 Evidence。
- `config_snapshot` 只保存 `query_sha256`，不保存原始 query。
- 采集进度按三种记录区分：`raw_signal`（全局、内容寻址、不可变快照）、
  `raw_signal_observation`（每次 run/scope/week 对某个快照的观察归属）、
  `source_cursor`（按 `(source, scope_key)` 保存查询进度）。同一个快照被多个
  scope 或多个周次观察时不会重复写入 `raw_signal`，而是各自记录一条 observation，
  因此不能仅靠 `raw_signal.collection_run_id` 来隔离多个 scope。
- `main.py` 和 GitHub Actions **仍未切换**。

#### materialize 命令

```bash
PYTHONPATH=src python -m ai_signal materialize github \
  --db-path ./.ai-signal/ai_signal.db \
  --week-key 2026-W33 \
  --scope-key ai-agents-v1 \
  --output-root ./.ai-signal \
  --limit 10 \
  --allow-output-write
```

- 缺少 `--allow-output-write` 时返回退出码 `4`，不创建目录、不改数据库、不写 Markdown。
- 仅在 `output-root/Inbox/` 下写入 `<material_pack_id>.md`，文件名来自稳定哈希 ID。
- 重复运行不新增文件；人工填写的六个反馈字段被保留，只更新机器生成正文。
- CLI 输出只含安全计数，不含路径、URL、payload、query 或 scope_key。

#### feedback sync 命令

```bash
PYTHONPATH=src python -m ai_signal feedback sync \
  --db-path ./.ai-signal/ai_signal.db \
  --inbox-dir ./.ai-signal/Inbox \
  --allow-feedback-write
```

- 缺少 `--allow-feedback-write` 时返回退出码 `4`，不改数据库。
- 仅扫描 inbox 第一层 `.md` 文件，不递归，不读 symlink。
- 输出仅含 `scanned`、`inserted`、`skipped`、`invalid` 计数。
