import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# --- 新版 SDK 导入 ---
from google import genai
from tavily import TavilyClient

# ================= 配置区 =================
# 调试开关：True = 使用假数据，False = 真实搜索
TEST_MODE = os.getenv("TEST_MODE", "False").lower() == "true"

# 获取 API Keys
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# 邮件配置 (163邮箱)
EMAIL_HOST = "smtp.163.com"
EMAIL_PORT = 465
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = os.getenv("EMAIL_TO")

# ================= 核心函数 =================

def get_mock_news():
    """返回假数据用于测试"""
    print("⚠️ 正在运行测试模式，使用模拟数据...")
    return [
        {"title": "Gemini 3.0 发布", "url": "https://google.com/news1", "content": "Google 发布了最新的 Gemini 3.0 Flash 模型..."},
        {"title": "DeepMind 新突破", "url": "https://deepmind.com/news", "content": "AI 在科学研究领域取得新进展..."}
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
    """使用 Gemini 3.0 Flash 总结 (新版 SDK)"""
    if not news_items:
        return "本周没有找到相关新闻。"

    print("🤖 正在调用 Gemini 3.0 Flash 进行总结...")
    
    # 构建 Prompt
    news_content = "\n".join([f"- [{item['title']}]({item['url']}): {item['content']}" for item in news_items])
    
    prompt = f"""
    你是一个专业的科技主编。请根据以下 AI 新闻简讯，写一份中文周报。
    
    【要求】
    1. 标题清晰，分为【重磅头条】、【技术前沿】、【行业动态】三部分。
    2. 语言简练专业。
    3. 每条新闻必须附带原文链接。
    4. 结尾简述本周趋势。

    【新闻数据】
    {news_content}
    """

    try:
        # --- 新版 SDK 调用逻辑 (2026) ---
        # 1. 初始化客户端
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        # 2. 生成内容 (注意方法名变化: models.generate_content)
        # 尝试使用 3.0 Flash，如果您的 Key 权限受限，可回退到 gemini-2.0-flash
        response = client.models.generate_content(
            model="gemini-3.0-flash", 
            contents=prompt
        )
        
        # 3. 获取文本 (直接 .text)
        return response.text
        
    except Exception as e:
        print(f"❌ 总结失败: {e}")
        return f"总结生成出错: {e}"

def send_email(subject, body):
    """发送邮件"""
    if not EMAIL_USER or not EMAIL_PASS:
        print("⚠️ 未配置邮件账户，跳过发送。")
        print(body)
        return

    msg = MIMEMultipart()
    msg['From'] = EMAIL_USER
    msg['To'] = EMAIL_TO
    msg['Subject'] = subject
    
    html_content = f"""
    <div style="font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; line-height: 1.6; color: #333;">
        <h2 style="color: #2c3e50;">🤖 AI News Weekly</h2>
        <pre style="white-space: pre-wrap; font-family: inherit; font-size: 14px;">{body}</pre>
        <hr style="border: 0; border-top: 1px solid #eee; margin: 20px 0;">
        <p style="font-size: 12px; color: #999;">Powered by Gemini 3.0 & GitHub Actions</p>
    </div>
    """
    msg.attach(MIMEText(html_content, 'html', 'utf-8'))

    try:
        print(f"📧 正在连接 SMTP ({EMAIL_HOST})...")
        server = smtplib.SMTP_SSL(EMAIL_HOST, EMAIL_PORT)
        server.login(EMAIL_USER, EMAIL_PASS)
        server.send_message(msg)
        server.quit()
        print("✅ 邮件发送成功！")
    except Exception as e:
        print(f"❌ 邮件发送失败: {e}")

if __name__ == "__main__":
    keywords = "Artificial Intelligence LLM news last week"
    news_data = search_news(keywords)
    
    if news_data:
        report = summarize_news(news_data)
        today = datetime.now().strftime("%Y-%m-%d")
        send_email(f"AI周报 ({today})", report)
    else:
        print("没有获取到新闻数据。")