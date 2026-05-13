import os
import re
import json
import uuid
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, ImageMessage, TextSendMessage
from google.cloud import vision
from google.oauth2 import service_account
from supabase import create_client, Client

app = Flask(__name__)

# --- 1. การตั้งค่าเริ่มต้น ---
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))

# Google Vision
creds_dict = json.loads(os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON'))
creds = service_account.Credentials.from_service_account_info(creds_dict)
vision_client = vision.ImageAnnotatorClient(credentials=creds)

# Supabase
supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_ANON_KEY"))

# --- 2. ฟังก์ชันช่วยค้นหายอดเงิน ---
def extract_amount(text):
    if not text: return None
    # ดักจับรูปแบบยอดเงินทั่วไปในสลิปไทยและอังกฤษ
    patterns = [
        r'(?:จำนวนเงิน|ยอดเงิน|Amount|Total|Net Amount)\s*[:\-]?\s*([\d,]+\.\d{2})',
        r'([\d,]+\.\d{2})\s*(?:บาท|Baht|THB)'
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return float(match.group(1).replace(',', ''))
    return None

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except Exception:
        abort(400)
    return 'OK'

# --- 3. ส่วนประมวลผลรูปภาพสลิป ---
@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    user_id = event.source.user_id

    # A. ค้นหา Active Trip ของผู้ใช้ (อ้างอิงจากตาราง trips)
    trip_res = supabase.table("trips").select("id").eq("line_user_id", user_id).eq("status", "active").execute()
    
    if not trip_res.data:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ ไม่พบทริปที่กำลังใช้งาน พิมพ์ /newtrip เพื่อเริ่มทริปก่อนนะครับ"))
        return
    
    trip_id = trip_res.data[0]['id']

    # B. ดึงรูปภาพจาก LINE และทำ OCR ด้วย Google Vision
    message_content = line_bot_api.get_message_content(event.message.id)
    image_bytes = b''.join(message_content.iter_content())
    
    image = vision.Image(content=image_bytes)
    response = vision_client.text_detection(image=image)
    texts = response.text_annotations
    
    if not texts:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ อ่านข้อมูลรูปภาพไม่ได้ครับ"))
        return

    full_text = texts[0].description
    amount = extract_amount(full_text)

    if amount is not None:
        try:
            # C. อัปโหลดรูปไปที่ Supabase Storage (Bucket: slips)
            # ตั้งชื่อไฟล์เป็น ID ข้อความเพื่อไม่ให้ซ้ำ และเก็บใน Folder ตามทริป
            file_path = f"trips/{trip_id}/{event.message.id}.jpg"
            
            supabase.storage.from_('slips').upload(
                path=file_path,
                file=image_bytes,
                file_options={"content-type": "image/jpeg"}
            )

            # ดึง URL ของรูปที่อัปโหลด
            slip_url = supabase.storage.from_('slips').get_public_url(file_path)

            # D. บันทึกข้อมูลลงตาราง expenses (ตรงตาม Schema ในรูปของคุณ)
            data = {
                "trip_id": trip_id,
                "line_user_id": user_id,
                "amount": amount,
                "slip_url": slip_url,
                "raw_ocr_data": {"full_text": full_text},
                "item_name": "บันทึกจากสลิป" # สามารถปรับเป็น Logic ค้นหาชื่อร้านค้าเพิ่มเติมได้
            }
            
            supabase.table("expenses").insert(data).execute()
            
            reply = f"✅ บันทึกสำเร็จ!\n💰 ยอดเงิน: {amount:,.2f} บาท\n📂 ทริป ID: {trip_id}"
            
        except Exception as e:
            reply = f"⚠️ พบยอดเงิน {amount:,.2f} บาท แต่บันทึกลงระบบไม่ได้"
            print(f"Error: {e}")
    else:
        reply = "❓ ระบบไม่พบยอดเงินในสลิปใบนี้ กรุณาพิมพ์บันทึกเองนะครับ"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)