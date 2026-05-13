import os
import re
import json
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, ImageMessage, TextSendMessage
from google.cloud import vision
from google.oauth2 import service_account
from supabase import create_client, Client

app = Flask(__name__)

# --- 1. โหลด Config และตั้งค่าความปลอดภัย ---
def get_env_or_warn(key):
    val = os.getenv(key)
    if not val:
        print(f"⚠️ Warning: {key} is missing!")
    return val

# LINE Config
line_bot_api = LineBotApi(get_env_or_warn('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(get_env_or_warn('LINE_CHANNEL_SECRET'))

# Google Vision Config (ป้องกันแอปบึ้มถ้า JSON ผิด)
vision_client = None
google_creds_json = os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON')
if google_creds_json:
    try:
        creds_dict = json.loads(google_creds_json)
        creds = service_account.Credentials.from_service_account_info(creds_dict)
        vision_client = vision.ImageAnnotatorClient(credentials=creds)
    except Exception as e:
        print(f"❌ Vision API Error: {e}")

# Supabase Config (ป้องกันแอปบึ้มถ้า URL/Key หาย)
supabase = None
s_url = os.getenv("SUPABASE_URL")
s_key = os.getenv("SUPABASE_ANON_KEY")
if s_url and s_key:
    try:
        supabase: Client = create_client(s_url, s_key)
    except Exception as e:
        print(f"❌ Supabase Error: {e}")

# --- 2. ฟังก์ชันช่วยประมวลผล (Utility) ---
def extract_amount(text):
    """ ค้นหายอดเงินจากข้อความด้วย Regex """
    if not text: return None
    patterns = [
        r'(?:จำนวนเงิน|ยอดเงิน|Amount|Total|Net Amount)\s*[:\-]?\s*([\d,]+\.\d{2})',
        r'([\d,]+\.\d{2})\s*(?:บาท|Baht|THB)'
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return float(match.group(1).replace(',', ''))
    return None

# --- 3. Routes & Webhook ---
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except Exception:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    # ตรวจสอบความพร้อมของระบบ
    if not vision_client or not supabase:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ ระบบ Backend ไม่พร้อมใช้งาน กรุณาเช็คการตั้งค่า"))
        return

    # 1. ดึงข้อมูลรูปภาพจาก LINE
    message_content = line_bot_api.get_message_content(event.message.id)
    image_bytes = b''.join(message_content.iter_content())

    # 2. ส่งให้ Google Vision ทำ OCR
    image = vision.Image(content=image_bytes)
    response = vision_client.text_detection(image=image)
    texts = response.text_annotations
    
    if not texts:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="อ่านรูปไม่ได้ครับ รบกวนส่งสลิปที่ชัดเจนกว่านี้"))
        return

    full_text = texts[0].description
    amount = extract_amount(full_text)

    # 3. บันทึกข้อมูลลง Supabase
    if amount is not None:
        try:
            # ดึงชื่อ Profile (ถ้าดึงไม่ได้ให้ใช้ 'Anonymous')
            try:
                profile = line_bot_api.get_profile(event.source.user_id)
                user_name = profile.display_name
            except:
                user_name = "Anonymous"

            data = {
                "user_name": str(user_name),
                "amount": amount,
                "user_id": event.source.user_id
            }
            supabase.table("expenses").insert(data).execute()
            reply = f"✅ บันทึกสำเร็จ!\nผู้จ่าย: {user_name}\nยอดเงิน: {amount:,.2f} บาท"
        except Exception as e:
            reply = f"⚠️ อ่านยอดได้ {amount:,.2f} บาท แต่เซฟลง Database ไม่สำเร็จ"
            print(f"Database Insert Error: {e}")
    else:
        reply = "❌ ไม่พบยอดเงินในรูปภาพ โปรดลองใหม่อีกครั้ง"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)