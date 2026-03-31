import os
import re
import smtplib
import time
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import markdown2
import requests
from bs4 import BeautifulSoup
from google import genai
from openai import OpenAI
from tavily import TavilyClient

# ================= 配置区 =================
TEST_MODE = os.getenv("TEST_MODE", "False").lower() == "true"

# API Keys
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY")


# 邮件配置 (163邮箱)
EMAIL_HOST = "smtp.163.com"
EMAIL_PORT = 465
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = os.getenv("EMAIL_TO")

# AI 关键词过滤列表
AI_KEYWORDS = [
    "ai", "artificial intelligence", "llm", "large language model",
    "agent", "agents", "rag", "prompt", "gpt", "claude", "gemini",
    "deepseek", "transformer", "diffusion", "model", "inference",
    "machine learning", "ml", "nlp", "neural", "embedding",
    "fine-tun", "train", "openai", "anthropic", "huggingface",
    "langchain", "autogen", "crewai", "ollama", "vllm", "onnx",
    "chatbot", "copilot", "stable diffusion", "midjourney",
    "llama", "mistral", "bert", "gpt", "generative",
    "text-to-", "image-gen", "code-gen", "speech", "tts", "asr",
    "vector", "retrieval", "tokenizer", "moe", "mixture of expert",
    "多模态", "大模型", "智能体", "模型", "工具",
]


# ================= 日期工具 =================

def get_week_info():
    """获取上周年份、ISO周数、日期范围（周一发送上周的新闻）"""
    now = datetime.now()

    # 获取上周的日期范围
    # 先计算本周一，然后减去7天得到上周一
    this_monday = now - timedelta(days=now.weekday())
    last_monday = this_monday - timedelta(days=7)
    last_sunday = last_monday + timedelta(days=6)

    # 上周的ISO周数和年份
    last_iso = last_monday.isocalendar()
    year = last_iso[0]
    week_num = last_iso[1]

    date_range = f"{last_monday.strftime('%m/%d')}-{last_sunday.strftime('%m/%d')}"

    return {
        "year": year,
        "week_num": week_num,
        "date_range": date_range,
        "today": now.strftime("%Y-%m-%d"),
    }


# ================= 数据获取：Tavily 新闻 =================

def get_mock_news():
    return [
        {"title": "DeepSeek V3.2 发布", "url": "https://deepseek.com", "content": "DeepSeek 发布 V3.2 版本，性能超越 GPT-4，推理速度提升3倍，API价格降低50%。"},
        {"title": "OpenAI 推出 GPT-5 多模态能力", "url": "https://openai.com", "content": "GPT-5 新增原生图像理解和代码执行能力，API 开发者可立即使用。"},
        {"title": "Google Gemini 2.5 Pro 开源部分组件", "url": "https://blog.google", "content": "Google 开源 Gemini 2.5 Pro 的推理框架，社区反响热烈。"},
        {"title": "Meta 发布 Llama 4 Scout 开源模型", "url": "https://ai.meta.com", "content": "Llama 4 Scout 支持 10M token 上下文窗口，开源社区广泛采用。"},
        {"title": "Claude 新增 Tool Use 批处理模式", "url": "https://anthropic.com", "content": "Anthropic 为 Claude 添加批处理 Tool Use，API 调用效率提升5倍。"},
    ]


def search_news(queries):
    """使用 Tavily 多关键词搜索 AI 新闻，合并去重"""
    if TEST_MODE:
        return get_mock_news()

    if isinstance(queries, str):
        queries = [queries]

    all_results = []
    seen_urls = set()

    for query in queries:
        print(f"  [Tavily] 搜索: {query}...")
        for attempt in range(2):  # 每个查询最多重试2次
            try:
                tavily = TavilyClient(api_key=TAVILY_API_KEY)
                response = tavily.search(
                    query=query,
                    search_depth="basic",
                    topic="news",
                    days=7,
                    max_results=8,
                    include_raw_content=False,
                )
                results = response.get("results", [])
                for r in results:
                    url = r.get("url", "")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        all_results.append(r)
                print(f"    -> +{len(results)} 条 (累计 {len(all_results)} 条)")
                break
            except Exception as e:
                if attempt == 0:
                    print(f"    -> 失败，重试: {e}")
                    time.sleep(2)
                else:
                    print(f"    -> 重试仍失败: {e}")

    print(f"  [Tavily] 共获取 {len(all_results)} 条不重复新闻")
    return all_results


# ================= 数据获取：GitHub Trending =================

def get_mock_github_trending():
    return [
        {"name": "openai/codex", "url": "https://github.com/openai/codex",
         "description": "Lightweight coding agent that runs in your terminal",
         "language": "TypeScript", "stars_gained": "+2,340", "total_stars": "15.2k"},
        {"name": "langchain-ai/langgraph", "url": "https://github.com/langchain-ai/langgraph",
         "description": "Build resilient language agents as graphs",
         "language": "Python", "stars_gained": "+1,890", "total_stars": "12.8k"},
        {"name": "ollama/ollama", "url": "https://github.com/ollama/ollama",
         "description": "Get up and running with Llama 3, Mistral, and other LLMs locally",
         "language": "Go", "stars_gained": "+3,100", "total_stars": "105k"},
        {"name": "anthropics/claude-code", "url": "https://github.com/anthropics/claude-code",
         "description": "An agentic coding tool that lives in your terminal",
         "language": "Python", "stars_gained": "+1,500", "total_stars": "8.9k"},
        {"name": "vllm-project/vllm", "url": "https://github.com/vllm-project/vllm",
         "description": "High-throughput and memory-efficient inference and serving engine for LLMs",
         "language": "Python", "stars_gained": "+980", "total_stars": "42.3k"},
        {"name": "microsoft/autogen", "url": "https://github.com/microsoft/autogen",
         "description": "A programming framework for agentic AI",
         "language": "Python", "stars_gained": "+720", "total_stars": "45.1k"},
    ]


def is_ai_related(repo):
    """判断仓库是否与 AI 相关"""
    text = f"{repo.get('name', '')} {repo.get('description', '')}".lower()
    return any(kw in text for kw in AI_KEYWORDS)


def scrape_github_trending(since="weekly"):
    """抓取 GitHub Trending 页面中 AI 相关项目，多策略容错"""
    if TEST_MODE:
        return get_mock_github_trending()

    print(f"[2/3] 正在抓取 GitHub Trending ({since})...")

    # 策略 A: 直接抓取 HTML 页面
    repos = _scrape_github_html(since)

    # 策略 B: HTML 抓取失败，尝试 GitHub API 搜索热门项目
    if not repos:
        print("  -> HTML 抓取失败，尝试 GitHub API 搜索...")
        repos = _scrape_github_api(since)

    if not repos:
        print("  -> GitHub 数据获取全部失败")
        return []

    # AI 关键词过滤
    ai_repos = [r for r in repos if is_ai_related(r)]
    if len(ai_repos) < 5:
        print(f"  -> AI 项目偏少({len(ai_repos)}个)，补充热门项目")
        ai_repos = repos

    # 策略 C: 通过 GitHub API 补充每个仓库的 README 摘要 + 精确星数
    ai_repos = _enrich_repos_with_api(ai_repos[:15])

    print(f"  -> 获取到 {len(repos)} 个仓库，其中 {len([r for r in repos if is_ai_related(r)])} 个 AI 相关")
    return ai_repos


def _scrape_github_html(since="weekly"):
    """策略 A: 直接抓取 GitHub Trending HTML 页面"""
    try:
        resp = requests.get(
            f"https://github.com/trending?since={since}",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=15,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # 多种 selector 容错
        articles = soup.select("article.Box-row")
        if not articles:
            articles = soup.select("article")
        if not articles:
            articles = soup.select('[data-testid="repository-card"]') or soup.select("section.Box")

        if not articles:
            print(f"  -> HTML 解析未找到仓库条目（页面结构可能变化）")
            return []

        repos = []
        for article in articles:
            try:
                repo = _parse_github_article(article, since)
                if repo:
                    repos.append(repo)
            except Exception:
                continue

        return repos
    except Exception as e:
        print(f"  -> HTML 抓取失败: {e}")
        return []


def _parse_github_article(article, since):
    """从单个 article 元素解析仓库信息"""
    # 仓库名 - 多种 selector 容错
    h2 = article.select_one("h2 a") or article.select_one("h1 a")
    if not h2:
        return None
    href = h2.get("href", "").strip("/")
    if not href or "/" not in href:
        return None
    name = href.lstrip("/")

    # 描述 - 多种 selector
    desc_el = article.select_one("p") or article.select_one('[data-testid="repository-description"]')
    description = desc_el.get_text(strip=True) if desc_el else ""

    # 语言
    lang_el = article.select_one('[itemprop="programmingLanguage"]')
    if not lang_el:
        # 回退: 找包含颜色圆点的 span
        for span in article.select("span"):
            if span.select_one("circle") or span.select_one('[fill]'):
                lang_el = span
                break
    language = lang_el.get_text(strip=True) if lang_el else ""

    # 总星数 - 从 stargazers 链接提取
    total_stars = ""
    for link in article.select("a"):
        href = link.get("href", "")
        if "stargazers" in href:
            total_stars = link.get_text(strip=True).replace("\n", "").replace(" ", "").strip()
            break

    # 增长星数 - 多种正则模式
    stars_gained = ""
    all_text = article.get_text(separator=" ")

    patterns = [
        r"([\d,]+)\s*stars?\s*this\s*week",
        r"([\d,]+)\s*stars?\s*this\s*month",
        r"([\d,]+)\s*stars?\s*today",
        r"↑\s*([\d,]+)",  # 某些版本用箭头
    ]
    for pattern in patterns:
        match = re.search(pattern, all_text, re.I)
        if match:
            stars_gained = f"+{match.group(1)}"
            break

    return {
        "name": name,
        "url": f"https://github.com/{name}",
        "description": description,
        "language": language,
        "stars_gained": stars_gained,
        "total_stars": total_stars,
    }


def _scrape_github_api(since="weekly"):
    """策略 B: 使用 GitHub Search API 搜索本周热门 AI 项目"""
    try:
        # 计算日期范围
        days_map = {"daily": 1, "weekly": 7, "monthly": 30}
        days = days_map.get(since, 7)
        since_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        # GitHub search API: 按星数排序，最近创建或最近推送的
        queries = [
            f"stars:>100 pushed:>{since_date} topic:ai",
            f"stars:>100 pushed:>{since_date} topic:machine-learning",
            f"stars:>100 pushed:>{since_date} topic:llm",
        ]

        seen = set()
        repos = []
        for query in queries:
            if len(repos) >= 25:
                break
            resp = requests.get(
                "https://api.github.com/search/repositories",
                params={
                    "q": query,
                    "sort": "stars",
                    "order": "desc",
                    "per_page": 10,
                },
                headers={
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "AI-Weekly-Bot/1.0",
                },
                timeout=10,
            )
            if resp.status_code != 200:
                continue
            items = resp.json().get("items", [])
            for item in items:
                full_name = item.get("full_name", "")
                if full_name in seen:
                    continue
                seen.add(full_name)
                repos.append({
                    "name": full_name,
                    "url": item.get("html_url", f"https://github.com/{full_name}"),
                    "description": item.get("description", "") or "",
                    "language": item.get("language", "") or "",
                    "stars_gained": "",
                    "total_stars": str(item.get("stargazers_count", "")),
                })

        return repos
    except Exception as e:
        print(f"  -> GitHub API 搜索失败: {e}")
        return []


def _enrich_repos_with_api(repos):
    """通过 GitHub API 补充仓库的精确星数和 README 摘要"""
    print("  [3/3] 通过 GitHub API 补充仓库详情...")
    enriched = 0
    for repo in repos:
        name = repo.get("name", "")
        if not name or "/" not in name:
            continue
        try:
            # 获取仓库详情（精确星数、forks、topics）
            resp = requests.get(
                f"https://api.github.com/repos/{name}",
                headers={
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "AI-Weekly-Bot/1.0",
                },
                timeout=8,
            )
            if resp.status_code == 200:
                data = resp.json()
                # 更新精确星数
                repo["total_stars"] = str(data.get("stargazers_count", repo.get("total_stars", "")))
                repo["forks"] = str(data.get("forks_count", ""))
                repo["topics"] = ", ".join(data.get("topics", []))
                # 补充 description（API 的更完整）
                if data.get("description") and len(data.get("description", "")) > len(repo.get("description", "")):
                    repo["description"] = data["description"]

            # 获取 README 前200字符作为摘要
            readme_resp = requests.get(
                f"https://api.github.com/repos/{name}/readme",
                headers={
                    "Accept": "application/vnd.github.v3.raw",
                    "User-Agent": "AI-Weekly-Bot/1.0",
                },
                timeout=8,
            )
            if readme_resp.status_code == 200:
                readme_text = readme_resp.text.strip()
                # 提取第一段有意义的描述（跳过标题和徽章行）
                lines = []
                for line in readme_text.split("\n"):
                    line = line.strip()
                    if not line or line.startswith("#") or line.startswith("![") or line.startswith("[!") or line.startswith("<"):
                        continue
                    if line.startswith("["):
                        continue
                    lines.append(line)
                    if len(lines) >= 3:
                        break
                if lines:
                    summary = " ".join(lines)[:300]
                    repo["readme_summary"] = summary

            enriched += 1
            # 避免触发 GitHub API 速率限制（未认证60次/小时）
            time.sleep(1.5)
        except Exception:
            continue

    print(f"    -> 成功补充 {enriched}/{len(repos)} 个仓库的详情")
    return repos


# ================= Prompt 模板 =================

WEEKLY_REPORT_PROMPT = """你是一位专注AI领域的资深科技记者和工程师，风格像「量子位」+「IT咖啡馆」结合：专业、简洁、有洞见、实用导向、带从业者视角（为什么这东西值得我现在试？能帮我省时间/钱/提升效果？）。语言全程中文（简体），语气亲切但不水，零废话。

请根据以下【新闻数据】和【GitHub 项目数据】，生成一份 AI 周报。

## 红线规则（违反任何一条即为不合格输出）
1. **禁止捏造任何数据**：星数、链接、项目功能、上手命令——全部只能来自输入数据。输入中没有的信息，绝对不能编造。
2. **链接必须逐字复制**：每个 [文字](url) 中的 url 必须从输入数据的"链接:"行原样复制，一个字符都不能改。禁止使用"来源"作为链接文字。
3. **上手命令必须保守**：如果输入的README摘要里有具体安装命令，就用那个。如果没有，就写"详见仓库README"，绝不能编造 pip install/docker run 命令。
4. **星数必须如实**：输入写"未获取"就写"数据暂缺"，输入有数字就如实使用。绝对不能自己编一个数字。
5. **当数据缺失时的标准措辞**：
   - 星数缺失 → 写"热度数据暂缺"或"总星数暂缺"
   - 本周新增缺失 → 写"本周新增数据暂缺"
   - 链接缺失 → 写"来源未提供"
   - 描述缺失 → 写"暂无描述"

## 正确 vs 错误 示例

【新闻链接 - 正确】
- OpenAI 发布 GPT-5 [查看详情](https://openai.com/blog/gpt-5)  ← url 从输入"链接:"行原样复制
【新闻链接 - 错误】
- OpenAI 发布 GPT-5 来源  ← 错误！必须使用链接格式
- OpenAI 发布 GPT-5 [来源](链接)  ← 错误！url不能是"链接"两个字
- OpenAI 发布 GPT-5 [来源](https://www.openai.com/gpt-5)  ← 错误！不能自己构造url

【GitHub项目 - 正确】
上手：详见仓库 README，克隆后按文档指引配置即可。  ← 没有安装命令时保守写法
**总星数: 42,300**（输入提供），**本周新增: 数据暂缺**  ← 如实使用输入数据

【GitHub项目 - 错误】
上手：`pip install langgraph`  ← 错误！输入数据里没有这个命令，不能编造
**本周新增 +21,644 星**  ← 错误！输入写的是"未获取"，不能自己编数字

# 输出格式（严格遵循，不加任何开场白/结尾说明）

**本周一句话趋势洞察**
2-4句概括本周AI走向，有判断有观点。

**本周AI重磅动态**
三小节，总字数400-600字：

### 重磅头条
- 事件概括 + [查看详情](从输入的链接行原样复制url)

### 技术前沿
- 事件概括 + [查看详情](从输入的链接行原样复制url)

### 行业动态
- 事件概括 + [查看详情](从输入的链接行原样复制url)

**本周GitHub 5大AI热点项目**
精选5个（最热+最实用+最具新意），格式：

**1. [项目名](从输入的链接行原样复制url)**
一句话定位：基于输入的描述字段概括。
热度数据：只使用输入中的总星数和本周新增数据。如果"本周新增"是"未获取"或空值，写"本周新增: 数据暂缺"，不要编数字。
从业者价值：基于输入的描述和README摘要分析，能用来干嘛。
上手：有README安装信息就用，没有就写"详见仓库 README"。

（重复5次，编号1-5）

**实用AI技巧**
3-6条，每条1-2句。每条末尾标注（来自：新闻X）。不要凭空发明。

**推荐行动清单**
3-5条可执行建议 + 一句话预告下周方向。

## 写作规范
- 1200-1800字。
- **加粗**关键名词/数字。
- 用数据/事实说话，避免夸张词。
- GitHub项目不足5个时标注"精选X个"。

## 【新闻数据】
{news_content}

## 【GitHub 项目数据】
{github_content}
"""


# ================= Prompt 数据格式化 =================

def format_news_for_prompt(news_items):
    """将新闻列表格式化为 prompt 中的新闻数据段，URL 单独一行确保不丢失"""
    if not news_items:
        return "本周未获取到新闻数据。"
    lines = []
    for i, item in enumerate(news_items, 1):
        title = item.get("title", "无标题")
        url = item.get("url", "")
        content = item.get("content", "")
        lines.append(
            f"新闻{i}:\n"
            f"  标题: {title}\n"
            f"  链接: {url}\n"
            f"  内容: {content}"
        )
    return "\n".join(lines)


def format_github_for_prompt(repos):
    """将 GitHub 项目列表格式化为 prompt 中的项目数据段，包含 README 摘要"""
    if not repos:
        return "本周未获取到 GitHub 趋势数据。"
    lines = []
    for i, repo in enumerate(repos[:10], 1):
        name = repo.get("name", "unknown")
        url = repo.get("url", "")
        desc = repo.get("description", "无描述")
        lang = repo.get("language", "")
        stars = repo.get("stars_gained", "") or "未获取"
        total = repo.get("total_stars", "") or "未知"
        forks = repo.get("forks", "")
        topics = repo.get("topics", "")
        readme = repo.get("readme_summary", "")

        parts = [
            f"项目{i}:",
            f"  名称: {name}",
            f"  链接: {url}",
            f"  描述: {desc}",
            f"  语言: {lang}",
            f"  总星数: {total}",
        ]
        if stars != "未获取":
            parts.append(f"  本周新增星数: {stars}")
        if forks:
            parts.append(f"  Forks: {forks}")
        if topics:
            parts.append(f"  标签: {topics}")
        if readme:
            parts.append(f"  README摘要: {readme}")

        lines.append("\n".join(parts))
    return "\n\n".join(lines)


# ================= LLM 调用（四级容错） =================

def _call_llm(prompt):
    """三级 LLM 容错调用链：智谱 -> Gemini -> DeepSeek"""
    errors = []

    # 级别 1: 智谱 GLM-4-Flash（免费）
    if ZHIPU_API_KEY:
        print("  [LLM] 尝试智谱 GLM-4-Flash（免费）...")
        try:
            client = OpenAI(
                api_key=ZHIPU_API_KEY,
                base_url="https://open.bigmodel.cn/api/paas/v4/",
            )
            response = client.chat.completions.create(
                model="glm-4.7-flash",
                messages=[
                    {"role": "system", "content": "你是一位资深 AI 科技主编。CRITICAL: 严格遵守红线规则——禁止编造任何数据（星数、URL、命令）。输入中没有的信息写"数据暂缺"或"详见仓库 README"。链接必须从输入"链接:"行原样复制。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,  # 降低温度减少随机性
                stream=False,
            )
            print("  [LLM] 智谱 GLM-4-Flash 调用成功!")
            return response.choices[0].message.content
        except Exception as e:
            errors.append(f"[智谱 GLM-4-Flash] {e}")
            print(f"  [LLM] 智谱失败: {e}")

    # 级别 2: Gemini 3.0 Flash（免费额度）
    if GEMINI_API_KEY:
        print("  [LLM] 尝试 Gemini 3.0 Flash...")
        try:
            client = genai.Client(api_key=GEMINI_API_KEY)
            system_prompt = "CRITICAL: 严格遵守红线规则——禁止编造任何数据（星数、URL、命令）。输入中没有的信息写"数据暂缺"或"详见仓库 README"。链接必须从输入"链接:"行原样复制。"
            full_prompt = f"{system_prompt}\n\n{prompt}"
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=full_prompt,
            )
            print("  [LLM] Gemini 调用成功!")
            return response.text
        except Exception as e:
            errors.append(f"[Gemini] {e}")
            print(f"  [LLM] Gemini 失败: {e}")

    # 级别 3: DeepSeek（兜底）
    if DEEPSEEK_API_KEY:
        print("  [LLM] 尝试 DeepSeek（兜底）...")
        try:
            client = OpenAI(
                api_key=DEEPSEEK_API_KEY,
                base_url="https://api.deepseek.com",
            )
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": "CRITICAL: 严格遵守红线规则——禁止编造任何数据（星数、URL、命令）。输入中没有的信息写"数据暂缺"或"详见仓库 README"。链接必须从输入"链接:"行原样复制。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                stream=False,
            )
            print("  [LLM] DeepSeek 调用成功!")
            return response.choices[0].message.content
        except Exception as e:
            errors.append(f"[DeepSeek] {e}")
            print(f"  [LLM] DeepSeek 失败: {e}")

    # 全部失败
    error_report = "所有 LLM 模型均不可用:\n"
    for err in errors:
        error_report += f"  - {err}\n"
    error_report += "\n请检查 API Key 配置。"
    return error_report


def generate_report(news_items, github_repos):
    """组装 prompt 并调用 LLM 生成周报"""
    if not news_items and not github_repos:
        return "本周没有找到相关新闻和项目。"

    week_info = get_week_info()
    news_content = format_news_for_prompt(news_items)
    github_content = format_github_for_prompt(github_repos)

    prompt = WEEKLY_REPORT_PROMPT.format(
        year=week_info["year"],
        week_num=week_info["week_num"],
        date_range=week_info["date_range"],
        news_content=news_content,
        github_content=github_content,
    )

    print("[4/5] 正在生成周报...")
    return _call_llm(prompt)


# ================= 邮件 =================

def markdown_to_email_html(md_text):
    """将 Markdown 报告转换为 HTML"""
    html = markdown2.markdown(
        md_text,
        extras=["tables", "fenced-code-blocks", "strike", "task_list", "header-ids"],
    )
    return str(html)


def build_email_html(report_html, week_info):
    """构建带样式的邮件 HTML"""
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0; padding:0; background-color:#f6f8fa;">
<center>
  <table width="100%" cellpadding="0" cellspacing="0" style="max-width:680px; margin:0 auto;">
    <tr>
      <td align="center" style="padding:20px;">
        <!-- Header -->
        <div style="background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);
                    padding:24px 30px; border-radius:8px 8px 0 0;">
          <h1 style="color:#fff; margin:0; font-size:22px; font-family:sans-serif;">
            AI 周报 · {week_info['year']}-第{week_info['week_num']}周
          </h1>
          <p style="color:rgba(255,255,255,0.85); margin:6px 0 0; font-size:13px; font-family:sans-serif;">
            {week_info['date_range']}
          </p>
        </div>
        <!-- Body -->
        <div style="background:#ffffff; padding:28px 30px; text-align:left;
                    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
                    font-size:15px; line-height:1.8; color:#333;">
          {report_html}
        </div>
        <!-- Footer -->
        <div style="background:#f9fafb; padding:15px 30px; text-align:center;
                    font-size:12px; color:#999; border-radius:0 0 8px 8px;
                    font-family:sans-serif;">
          <p style="margin:0;">Generated by GitHub Actions | 数据来源: Tavily + GitHub Trending</p>
        </div>
      </td>
    </tr>
  </table>
</center>
</body>
</html>"""


def send_email(subject, body, is_html=True):
    """发送邮件"""
    if not EMAIL_USER or not EMAIL_PASS:
        print("未配置邮件账户，跳过发送。")
        print(f"\n{'='*60}")
        print(body if is_html else body)
        print(f"{'='*60}")
        return

    msg = MIMEMultipart()
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO
    msg["Subject"] = subject

    if is_html:
        msg.attach(MIMEText(body, "html", "utf-8"))
    else:
        html = f"""<div style="font-family:sans-serif; line-height:1.6; color:#333;">
            <pre style="white-space:pre-wrap; font-family:inherit; font-size:14px;">{body}</pre>
        </div>"""
        msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        print(f"[5/5] 正在发送邮件到 {EMAIL_TO}...")
        server = smtplib.SMTP_SSL(EMAIL_HOST, EMAIL_PORT)
        server.login(EMAIL_USER, EMAIL_PASS)
        server.send_message(msg)
        server.quit()
        print("  -> 邮件发送成功!")
    except Exception as e:
        print(f"  -> 邮件发送失败: {e}")


# ================= 主入口 =================

if __name__ == "__main__":
    print("=" * 50)
    print("AI 周报自动生成")
    print("=" * 50)

    # Step 1: 获取双数据源
    news_queries = [
        "AI artificial intelligence LLM news this week 2026",
        "AI agent RAG model release news",
        "AI open source tools framework update",
    ]
    news_data = search_news(news_queries)
    github_data = scrape_github_trending(since="weekly")

    # Step 2: 生成周报
    report = generate_report(news_data, github_data)

    # Step 3: 发送邮件
    if report and not report.startswith("所有 LLM"):
        week_info = get_week_info()
        report_html = markdown_to_email_html(report)
        email_html = build_email_html(report_html, week_info)
        subject = f"AI 周报 · {week_info['year']}-第{week_info['week_num']}周 | {week_info['date_range']}"
        send_email(subject, email_html, is_html=True)
    else:
        today = datetime.now().strftime("%Y-%m-%d")
        send_email(f"AI周报生成出错 ({today})", report, is_html=False)
