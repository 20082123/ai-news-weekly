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
