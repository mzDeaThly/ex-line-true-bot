import os
import sqlite3
import asyncio
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from playwright.async_api import async_playwright
from dotenv import load_dotenv

# ====== 1. การตั้งค่าและโหลดข้อมูลสำคัญ ======
load_dotenv()

# --- LINE OA & Admin Credentials ---
CHANNEL_ACCESS_TOKEN = os.getenv('CHANNEL_ACCESS_TOKEN')
CHANNEL_SECRET = os.getenv('CHANNEL_SECRET')
ADMIN_USER_ID = os.getenv('ADMIN_USER_ID') # User ID ของผู้ดูแลระบบ

# --- True Dealer Credentials ---
DEALER_USERNAME = os.getenv("DEALER_USERNAME")
DEALER_PASSWORD = os.getenv("DEALER_PASSWORD")

# --- Initialize Flask App and LINE SDK ---
app = Flask(__name__)
# ลบการสร้าง LineBotApi และ WebhookHandler ตรงนี้ เพื่อย้ายไปสร้างในฟังก์ชัน Handle_message แทน
# line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
# handler = WebhookHandler(CHANNEL_SECRET)

# --- Database Name ---
DB_NAME = 'users.db'

# ====== 2. ฟังก์ชันจัดการฐานข้อมูล (SQLite) ======
def init_db():
    """สร้างตารางในฐานข้อมูลหากยังไม่มี"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            expiration_date TEXT
        )
    ''')
    conn.commit()
    conn.close()

def add_user(user_id, days_to_expire):
    """เพิ่มหรืออัปเดตผู้ใช้และวันหมดอายุ"""
    expiration_date = datetime.now() + timedelta(days=int(days_to_expire))
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO users (user_id, expiration_date) VALUES (?, ?)",
                   (user_id, expiration_date.strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    conn.close()
    return expiration_date.strftime('%d/%m/%Y')

def is_user_valid(user_id):
    """ตรวจสอบว่าผู้ใช้มีสิทธิ์ใช้งานและยังไม่หมดอายุหรือไม่"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT expiration_date FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    if result:
        expiration_date = datetime.strptime(result[0], '%Y-%m-%d %H:%M:%S')
        if datetime.now() < expiration_date:
            return True, "VALID" # ใช้งานได้
    return False, "NOT_FOUND" if not result else "EXPIRED"

# ====== 3. ฟังก์ชันค้นหาข้อมูลลูกค้า (Web Scraping) ======
async def search_user_info(fname, lname, phone):
    # (โค้ดส่วนนี้เหมือนเดิม ไม่มีการเปลี่ยนแปลง)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'])
        page = await browser.new_page()
        print("[LOG] กำลังล็อกอิน...")
        await page.goto("https://wzzo.truecorp.co.th/auth/realms/Dealer-Internet/protocol/openid-connect/auth?client_id=crmlite-prod-dealer&response_type=code&scope=openid%20profile&redirect_uri=https://crmlite-dealer.truecorp.co.th/&state=xyz&nonce=abc&response_mode=query&code_challenge_method=S256&code_challenge=AzRSFK3CdlHMiDq1DsuRGEY-p6EzTxexaIRyLphE9o4", timeout=60000)
        await page.fill('input[name="username"]', DEALER_USERNAME)
        await page.fill('input[name="password"]', DEALER_PASSWORD)
        await page.click('input[type="submit"]')
        print("[LOG] กำลังไปที่หน้าค้นหา...")
        await page.goto("https://crmlite-dealer.truecorp.co.th/SmartSearchPage", timeout=60000)
        try:
            await page.locator('button:has-text("OK")').click(timeout=5000)
        except Exception: pass
        search_box_selector = "#SearchInput"
        await page.wait_for_selector(search_box_selector, timeout=60000)
        search_value = phone if phone else f"{fname} {lname}"
        await page.fill(search_box_selector, search_value)
        await page.press(search_box_selector, 'Enter')
        result_list_selector = "div[role='button']"
        await page.wait_for_selector(result_list_selector, timeout=30000)
        await page.locator(result_list_selector).first.click()
        await page.wait_for_url("**/AssetProfilePage", timeout=30000)
        await page.wait_for_selector("div.asset-info", timeout=10000)
        billing_info = await page.inner_text("div.asset-info")
        await browser.close()
        return billing_info

# ====== 4. ส่วนเชื่อมต่อกับ LINE (Webhook) ======
# สร้าง handler และ line_bot_api ในภายหลัง
handler = WebhookHandler(CHANNEL_SECRET)
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# ====== 5. ส่วนจัดการข้อความที่ได้รับ (Logic หลัก) ======
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_message = event.message.text.strip()
    reply_token = event.reply_token
    user_id = event.source.user_id
    
    # --- A: ตรวจสอบว่าเป็นคำสั่งจาก Admin หรือไม่ ---
    if user_id == ADMIN_USER_ID and user_message.lower().startswith('add '):
        try:
            parts = user_message.split()
            target_user_id = parts[1]
            days = int(parts[2])
            new_expiry_date = add_user(target_user_id, days)
            reply_text = f"✅ เพิ่มผู้ใช้สำเร็จ!\nUser ID: {target_user_id}\nหมดอายุใน: {days} วัน ({new_expiry_date})"
        except Exception as e:
            reply_text = f"❌ รูปแบบคำสั่งผิดพลาด\nโปรดใช้: add <UserID> <จำนวนวัน>\nเช่น: add U123... 30\n\nError: {e}"
        line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_text))
        return

    # --- B: ตรวจสอบสิทธิ์ผู้ใช้ทั่วไป ---
    is_valid, status = is_user_valid(user_id)
    if not is_valid:
        reply_text = ""
        if status == "EXPIRED":
            reply_text = "❌ บัญชีของคุณหมดอายุแล้ว กรุณาติดต่อผู้ดูแล"
        elif status == "NOT_FOUND":
            # --- [การแก้ไข] ---
            # เปลี่ยนข้อความตอบกลับสำหรับผู้ใช้ใหม่
            reply_text = "❌ คุณไม่มีสิทธิ์ใช้งานระบบนี้ กรุณาติดต่อ Admin Line : mzdear"
            
            # แจ้งเตือน Admin เมื่อมีผู้ใช้ใหม่ (ยังทำงานเหมือนเดิม)
            try:
                profile = line_bot_api.get_profile(user_id)
                admin_notification = f"แจ้งเตือน! มีผู้ใช้ใหม่พยายามใช้งานบอท\n\nชื่อ: {profile.display_name}\nUser ID: {user_id}"
                line_bot_api.push_message(ADMIN_USER_ID, TextSendMessage(text=admin_notification))
            except Exception as e:
                print(f"ไม่สามารถส่งข้อความหาแอดมินได้: {e}")
        
        line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_text))
        return

    # --- C: ถ้ามีสิทธิ์ ให้เริ่มการค้นหา ---
    # ... (ส่วนนี้ของโค้ดทำงานเหมือนเดิม) ...
    search_phone = ""
    search_fname = ""
    search_lname = ""
    if user_message.isdigit() and len(user_message) in [9, 10]:
        search_phone = user_message
    elif ' ' in user_message:
        parts = user_message.split(' ', 1)
        search_fname = parts[0]
        search_lname = parts[1]
    else:
        line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ รูปแบบไม่ถูกต้อง\nกรุณาส่งเบอร์โทรศัพท์ หรือ ชื่อ วรรค นามสกุล"))
        return

    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"🔍 รับทราบ! กำลังค้นหาข้อมูลสำหรับ '{user_message}'..."))
    
    try:
        billing_result = asyncio.run(search_user_info(fname=search_fname, lname=search_lname, phone=search_phone))
        result_text = f"📄 ผลการค้นหาสำหรับ: {user_message}\n\n{billing_result}"
        line_bot_api.push_message(user_id, TextSendMessage(text=result_text))
    except Exception as e:
        print(f"[ERROR] เกิดข้อผิดพลาด: {e}")
        line_bot_api.push_message(user_id, TextSendMessage(text=f"‼️ เกิดข้อผิดพลาดระหว่างการค้นหา:\n\n{e}"))

# ====== 6. ส่วนสำหรับรัน Web Server ======
if __name__ == "__main__":
    init_db() # สร้างไฟล์และตาราง database เมื่อเริ่มโปรแกรม
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
