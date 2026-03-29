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
    """获取当前年份、ISO周数、日期范围"""
    now = datetime.now()
    iso = now.isocalendar()
    year = iso[0]
    week_num = iso[1]
    # 计算本周周一
    monday = now - timedelta(days=now.weekday())
    sunday = monday + timedelta(days=6)
    date_range = f"{monday.strftime('%m/%d')}-{sunday.strftime('%m/%d')}"
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

    print(f"[2/2] 正在抓取 GitHub Trending ({since})...")

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


# ================= Prompt 模板 =================

WEEKLY_REPORT_PROMPT = """你是一位专注AI领域的资深科技记者和工程师，风格像「量子位」+「IT咖啡馆」结合：专业、简洁、有洞见、实用导向、带从业者视角（为什么这东西值得我现在试？能帮我省时间/钱/提升效果？）。语言全程中文（简体），语气亲切但不水，零废话。

请根据以下【新闻数据】和【GitHub 项目数据】，生成一份 AI 周报（针对AI工程师、研究员、独立开发者）。

## 红线规则（必须遵守）
1. **禁止捏造**：只从提供的【新闻数据】和【GitHub 项目数据】中提取信息。不要编造项目细节、星数、链接或功能描述。如果数据中缺少某个字段（如星数），就写"数据暂缺"或跳过该细节。
2. **禁止编造链接**：所有链接必须来自输入数据中已有的URL，不要自己构造github链接或新闻链接。
3. **禁止幻觉技巧**：实用技巧必须明确关联到上面某条新闻或某个GitHub项目，不要凭空发明不存在的功能。

# 严格输出格式（必须100%遵守，不要加任何开场白、结尾说明、多余文字。只输出以下结构，使用Markdown格式，便于邮件HTML转换）

# AI 周报 · {year}-第{week_num}周 | {date_range}

**本周一句话趋势洞察**
一句话总结本周AI整体走向（前沿/痛点/机会），2-4句，带从业者视角。

**本周AI重磅动态**
分成三小节，每条1-3句 + 链接，控制总字数400-600字：

### 重磅头条
- 事件概括 + [来源](链接)
  关键信息、影响、为什么重要。

### 技术前沿
- ...

### 行业动态
- ...

**本周GitHub 5大AI热点项目**
固定精选5个（从输入中选最热+最实用+最具新意的），每个项目格式严格如下：

**1. 项目名 [owner/repo](链接必须用输入数据中的url)**
一句话定位：做什么的。
为什么本周爆火：星增长/讨论热度/解决什么真实痛点（用输入中的实际数据，没有就写"近期关注度快速上升"）。
对AI从业者的价值：能用来干嘛？比现有方案好在哪里？立即可行动的场景。
上手一句话：具体的 pip install / docker run / npx 命令，或者核心试用步骤。

（重复5次，编号1-5）

**从本周动态 & 项目中提炼的实用AI技巧**
3-6条bullet points，每条短小精悍（1-2句），强调"立即可试""节省XX""提升XX"。
来源必须来自上面的新闻或5个项目，不要凭空发明。每条技巧末尾标注来源（来自哪个新闻或项目）。

**本周推荐行动清单**
- 3-5条具体、可执行的建议，例如：star并试用前3个项目中的本地部署功能；周末花1小时测试第2个技巧。
一句话预告下周可能热点方向。

## 写作规范
- 总字数控制在1200-1800字（邮件友好长度）。
- 所有链接用Markdown格式 [文字](url)，url必须来自输入数据。
- 用**加粗**突出关键名词/项目/数字。
- 语言生动但专业，避免"惊爆""碾压"等夸张词，用数据/事实说话。
- 如果输入中GitHub项目少于5个，从新闻中补充或标注"本周热点较少，精选X个"。
- 只输出以上内容，不要任何prompt相关说明或代码块外文字。

## 【新闻数据】
{news_content}

## 【GitHub 项目数据】
{github_content}
"""


# ================= Prompt 数据格式化 =================

def format_news_for_prompt(news_items):
    """将新闻列表格式化为 prompt 中的新闻数据段"""
    if not news_items:
        return "本周未获取到新闻数据。"
    lines = []
    for item in news_items:
        title = item.get("title", "无标题")
        url = item.get("url", "")
        content = item.get("content", "")
        lines.append(f"- [{title}]({url}): {content}")
    return "\n".join(lines)


def format_github_for_prompt(repos):
    """将 GitHub 项目列表格式化为 prompt 中的项目数据段，传前10个给 LLM 精选5个"""
    if not repos:
        return "本周未获取到 GitHub 趋势数据。"
    lines = []
    for i, repo in enumerate(repos[:10], 1):
        name = repo.get("name", "unknown")
        url = repo.get("url", "")
        desc = repo.get("description", "无描述")
        lang = repo.get("language", "")
        stars = repo.get("stars_gained", "")
        total = repo.get("total_stars", "")
        lines.append(
            f"{i}. [{name}]({url}) | {desc} | "
            f"语言: {lang} | 本周新增: {stars} | 总星数: {total}"
        )
    return "\n".join(lines)


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
                    {"role": "system", "content": "你是一位资深 AI 科技主编，专注AI领域的科技记者和工程师。"},
                    {"role": "user", "content": prompt},
                ],
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
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
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
                    {"role": "system", "content": "你是一位资深 AI 科技主编，专注AI领域的科技记者和工程师。"},
                    {"role": "user", "content": prompt},
                ],
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

    print("[3/4] 正在生成周报...")
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
        print(f"[4/4] 正在发送邮件到 {EMAIL_TO}...")
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
