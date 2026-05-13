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

# --- 1. ตั้งค่าการเชื่อมต่อ (Config) ---
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))

# Google Vision (อ่าน JSON จาก Env Var)
vision_client = None
try:
    google_creds_json = os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON')
    if google_creds_json:
        google_creds = json.loads(google_creds_json)
        credentials = service_account.Credentials.from_service_account_info(google_creds)
        vision_client = vision.ImageAnnotatorClient(credentials=credentials)
except Exception as e:
    print(f"Google Vision Config Error: {e}")

# Supabase
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_ANON_KEY")
supabase: Client = create_client(supabase_url, supabase_key)

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except Exception as e:
        print(f"Handler Error: {e}")
        abort(400)
    return 'OK'

def extract_amount(text):
    if not text:
        return None
    patterns = [
        r'(?:จำนวนเงิน|ยอดเงิน|Amount|Total|Net Amount)\s*[:\-]?\s*([\d,]+\.\d{2})',
        r'([\d,]+\.\d{2})\s*(?:บาท|Baht|THB)'
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return float(match.group(1).replace(',', ''))
    return None

@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    # --- จุดที่ 1: ป้องกัน user_name เป็น None ---
    user_name = "ผู้ใช้งาน"
    try:
        profile = line_bot_api.get_profile(event.source.user_id)
        if profile.display_name:
            user_name = profile.display_name
    except Exception as e:
        print(f"Get Profile Error: {e}")

    # --- จุดที่ 2: เช็คความพร้อมของ Vision Client ---
    if not vision_client:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="ระบบอ่านรูปภาพยังไม่พร้อมใช้งาน (Vision API Config Error)"))
        return

    # 2. ดึงรูปภาพ
    try:
        message_content = line_bot_api.get_message_content(event.message.id)
        image_bytes = b''.join(message_content.iter_content())
    except Exception as e:
        print(f"Get Content Error: {e}")
        return

    # 3. ส่งให้ Google Vision
    image = vision.Image(content=image_bytes)
    response = vision_client.text_detection(image=image)
    texts = response.text_annotations
    
    if not texts:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="อ่านรูปไม่ได้ครับ รบกวนส่งสลิปที่ชัดเจนกว่านี้"))
        return

    full_text = texts[0].description
    amount = extract_amount(full_text)

    # --- จุดที่ 3: ใช้ f-string ป้องกัน TypeError ตอนต่อ String ---
    if amount is not None:
        try:
            data = {
                "user_name": str(user_name), # บังคับเป็น string
                "amount": amount,
                "user_id": event.source.user_id
            }
            supabase.table("expenses").insert(data).execute()
            reply_text = f"✅ บันทึกสำเร็จ!\nผู้จ่าย: {user_name}\nยอดเงิน: {amount:,.2f} บาท"
        except Exception as e:
            print(f"Supabase Error: {e}")
            reply_text = f"⚠️ อ่านยอดได้ {amount:,.2f} บาท แต่บันทึกลงฐานข้อมูลไม่สำเร็จ"
    else:
        reply_text = "❌ พบรูปภาพแต่ไม่พบยอดเงิน โปรดพิมพ์ระบุยอดเงินด้วยตัวเอง หรือส่งรูปที่ชัดเจนกว่านี้"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

if __name__ == "__main__":
    # สำหรับ Render ต้องระบุ Port
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)