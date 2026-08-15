# GS-12 · hax v0.3.0（终端原生 C 语言 coding agent）

- **状态**：草案档案；第一用户初判已记录（见文末）
- **第一方证据**：
  - v0.3.0 Release：https://github.com/OleksandrChekhovskyi/hax/releases/tag/v0.3.0
  - v0.2.0 Release：https://github.com/OleksandrChekhovskyi/hax/releases/tag/v0.2.0
  - 设计文档：https://github.com/OleksandrChekhovskyi/hax/blob/master/docs/philosophy.md
  - 采集快照（2026-08-15）：223★，活跃 push

## 一句话判断（可争论、可验证）

> 一个 223★ 的终端 Agent 试图用「单一 C 二进制 + Unix 组合 + 不设权限确认」换取更简单、
> 可审计的使用边界，明确不复制 MCP/插件生态——这是设计取舍，不是功能缺失。

## 事件与时间线

| 时间 | 事件 |
|---|---|
| 2026-08-08 | v0.2.0：静态 Linux 二进制（x86_64/aarch64）+ SHA256SUMS；TLS CA 自动发现；内置 diff，去 diffutils 依赖 |
| 2026-08-12 | v0.3.0：Homebrew tap；`/session` 请求前显示上下文用量；`task_kill` 并入 `task_wait`；去掉内置默认模型名（默认值由 llama.cpp 发现或 `~/.codex/config.toml` 镜像决定）；编辑器/分页器回退链 |
| 2026-08-15 | 仍在活跃 push（本周采集快照） |

## 目标用户与任务

- 谁：在终端完成 AI 辅助编码的技术工作者（程序员、独立开发者、小团队）。
- 什么任务：装一个边界清晰、依赖少、能"脚本套脚本"的 coding agent；失败时能自己恢复。

## Claims 与 Evidence

| Claim | 证据 | 关系 | 可用措辞 |
|---|---|---|---|
| v0.3.0 发布内容（Homebrew、/session、任务停止合并等） | v0.3.0 Release notes | supports | "Release notes 写明……" |
| 项目明确不实现 MCP、hooks/plugins、权限确认、自定义斜杠命令，并逐条给替代方案 | philosophy.md | supports | "项目文档明确选择了……" |
| 扩展性 = CLI 工具 + SKILL.md + 外部脚本驱动 `hax -p` | philosophy.md | supports | "设计文档主张……" |
| 无权限确认 = 更安全 | —— | contradicts（官方自认同进程 gate 非安全边界） | 不能写"更安全" |
| 默认模型来自 `~/.codex/config.toml` 镜像 | v0.3.0 Changed 节 | supports | 事实可转述 |
| 更好用、更快、比 XX 强 | 无第三方/实测证据 | 缺证据 | 禁说 |

## 限制、反例和未知

1. Windows 未覆盖：官方静态二进制只列 Linux x86_64/aarch64（+ Homebrew）；Windows 需 WSL/编译。
2. "无权限确认"是双刃剑：官方自述它不是安全边界，靠提示词约束 + Esc 暂停 + max_turns 兜底。
3. 无独立测评：速度、成功率、与 Codex/Claude Code 的对比全部未知。

## 本人测试

- 命题 A（设计取舍）不必亲测。
- 命题 B（好装/好用/适合日常）必须亲测：WSL2/macOS 安装 → 修失败测试循环 → `/session` 观察 → 打断测 `task_wait kill`；记录成功率、耗时、接管次数、失败点。

## Editorial Decision 草案

| 命题 | 判定 | 理由 |
|---|---|---|
| A：「用更少扩展机制换简单边界，并解释每一项取舍」 | ready_to_write | 版本 + 官方设计文档足以支撑 |
| B：「比复杂框架更好装、更稳定、适合日常终端任务」 | needs_testing | 体验型结论需实测 |

## 禁说清单

- 不能说"更安全"（官方自述 gate 非安全边界）。
- 不能说"爆火/快速增长"（223★ 单点快照）。
- 不能说"Windows 开箱即用"。
- 不能把 GitHub 单一来源说成"普通用户普遍采用"。

## 第一用户判定（2026-08-15，聊天记录）

> 用户原话："这条对我没有吸引。"

- 初判方向：**reject**（对第一用户无吸引力；命题本身成立，但受众不匹配第一用户兴趣）。
- 教训归档：技术正确 ≠ 第一用户想做。候选筛选应加入"个人兴趣"这一人工权重。

## Golden Set 标注字段（草案，待第一用户确认）

| 字段 | 值 |
|---|---|
| example_id | GS-12A / GS-12B |
| event_id | hax-v0.3.0 |
| event | hax v0.2.0 → v0.3.0 两个 Release |
| content_proposition | A：设计取舍；B：体验型宣称 |
| change_type | Tool / Workflow |
| primary_audience | 终端完成 AI 任务的开发者 |
| job_to_be_done | 选择边界清晰的终端 Agent |
| primary_evidence | v0.3.0 Release + philosophy.md |
| missing_evidence | 独立测评（B 命题） |
| expected_qualification | research |
| expected_editorial | A：ready_to_write；B：needs_testing |
| why | 第一用户初判：不吸引（reject） |
| content_outcome | 未采用 |
