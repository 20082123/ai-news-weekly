import os
import smtplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# --- 第三方库 ---
import google.generativeai as genai
from tavily import TavilyClient

# ================= 配置区 =================
# 调试开关：True = 使用假数据（不消耗 Tavily 额度），False = 真实搜索
# 建议：本地测试设为 True，上传到 GitHub 前改为 False (或者通过环境变量控制)
TEST_MODE = os.getenv("TEST_MODE", "False").lower() == "true"

# 获取 API Keys (从环境变量)
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# 邮件配置
EMAIL_HOST = "smtp.163.com"  # 推荐使用 QQ (smtp.qq.com) 或 163 (smtp.163.com)
EMAIL_PORT = 465            # SSL 端口
EMAIL_USER = os.getenv("EMAIL_USER")     # 发件人邮箱
EMAIL_PASS = os.getenv("EMAIL_PASS")     # 邮箱授权码 (不是登录密码!)
EMAIL_TO = os.getenv("EMAIL_TO")         # 收件人邮箱

# ================= 核心函数 =================

def get_mock_news():
    """返回假数据用于测试"""
    print("⚠️ 正在运行测试模式，使用模拟数据...")
    return [
        {"title": "Google 发布 Gemini 1.5 Flash", "url": "https://google.com/news1", "content": "Google 推出了更快速、更便宜的模型..."},
        {"title": "OpenAI 推出新功能", "url": "https://openai.com/news2", "content": "ChatGPT 现在支持更多模态..."},
        {"title": "GitHub Copilot 升级", "url": "https://github.com/news3", "content": "编程助手现在能理解整个代码库..."}
    ]

def search_news(query):
    """使用 Tavily 搜索"""
    if TEST_MODE:
        return get_mock_news()

    print(f"🔍 正在调用 Tavily 搜索: {query}...")
    try:
        tavily = TavilyClient(api_key=TAVILY_API_KEY)
        response = tavily.search(
            query=query,
            search_depth="basic",
            topic="news",
            days=7,
            max_results=7,
            include_raw_content=False
        )
        return response.get('results', [])
    except Exception as e:
        print(f"❌ 搜索失败: {e}")
        return []

def summarize_news(news_items):
    """使用 Gemini 总结"""
    if not news_items:
        return "本周没有找到相关新闻。"

    print("🤖 正在调用 Gemini 1.5 Flash 进行总结...")
    
    # 构建上下文
    news_content = "\n".join([f"- [{item['title']}]({item['url']}): {item['content']}" for item in news_items])
    
    prompt = f"""
    你是一个专业的科技主编。请根据以下 AI 新闻简讯，写一份中文周报。
    
    【要求】
    1. 标题清晰，分为【重磅头条】、【技术前沿】、【行业动态】三部分。
    2. 语言简练专业。
    3. 每条新闻必须附带原文链接。
    4. 最后加一句"本周总结"作为结尾。

    【新闻数据】
    {news_content}
    """

    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"❌ 总结失败: {e}")
        return f"总结生成出错: {e}"

def send_email(subject, body):
    """发送邮件"""
    if not EMAIL_USER or not EMAIL_PASS:
        print("⚠️ 未配置邮件账户，跳过发送。")
        print("----- 生成的周报内容 -----")
        print(body)
        print("------------------------")
        return

    msg = MIMEMultipart()
    msg['From'] = EMAIL_USER
    msg['To'] = EMAIL_TO
    msg['Subject'] = subject
    
    # 简单的 Markdown 转 HTML 样式
    html_content = f"""
    <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <h2>🤖 AI News Weekly</h2>
        <pre style="white-space: pre-wrap; font-family: inherit;">{body}</pre>
        <hr>
        <p style="font-size: 12px; color: #888;">Powered by Gemini 1.5 & GitHub Actions</p>
    </div>
    """
    msg.attach(MIMEText(html_content, 'html', 'utf-8'))

    try:
        print(f"📧 正在连接 SMTP 服务器 ({EMAIL_HOST})...")
        server = smtplib.SMTP_SSL(EMAIL_HOST, EMAIL_PORT)
        server.login(EMAIL_USER, EMAIL_PASS)
        server.send_message(msg)
        server.quit()
        print("✅ 邮件发送成功！")
    except Exception as e:
        print(f"❌ 邮件发送失败: {e}")

if __name__ == "__main__":
    # 1. 搜索
    keywords = "Artificial Intelligence LLM news last week"
    news_data = search_news(keywords)
    
    # 2. 总结
    if news_data:
        report = summarize_news(news_data)
        
        # 3. 发送
        today = datetime.now().strftime("%Y-%m-%d")
        send_email(f"AI周报 ({today})", report)
    else:
        print("没有获取到新闻数据。")