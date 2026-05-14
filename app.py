import os
import re
import json
import uuid
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
# แก้ไข: เพิ่ม TextMessage เพื่อรองรับการรับข้อความคำสั่ง
# แก้ไข: เพิ่ม TextSendMessage สำหรับส่งข้อความตอบกลับ
from linebot.models import MessageEvent, ImageMessage, TextMessage, TextSendMessage
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

# --- 2. ฟังก์ชันช่วย (Helper Functions) ---
# เพิ่มเติม: ฟังก์ชันสำหรับเช็คทริปที่ยัง Active อยู่ เพื่อลดความซ้ำซ้อนของโค้ด
def get_active_trip(user_id):
    res = supabase.table("trips").select("id").eq("line_user_id", user_id).eq("status", "active").execute()
    return res.data[0]['id'] if res.data else None

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

# --- 3. ส่วนจัดการคำสั่งด้วยข้อความ (New Text Handler) ---
# เพิ่มเติม: ส่วนนี้ถูกเพิ่มเข้ามาใหม่ทั้งหมดเพื่อรองรับคำสั่งจัดการทริป
@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    text = event.message.text.strip()
    user_id = event.source.user_id

    # คำสั่งสร้างทริปใหม่: /newtrip [ชื่อทริป]
    if text.startswith('/newtrip'):
        trip_name = text.replace('/newtrip', '').strip() or "ทริปใหม่"
        # Logic: ปิดทริปเก่าที่ยังค้างอยู่ก่อนสร้างทริปใหม่
        supabase.table("trips").update({"status": "completed"}).eq("line_user_id", user_id).eq("status", "active").execute()
        # บันทึกทริปใหม่ลง Database
        supabase.table("trips").insert({"line_user_id": user_id, "trip_name": trip_name, "status": "active"}).execute()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🚀 เริ่มทริปใหม่: {trip_name} เรียบร้อย!"))

    # คำสั่งปิดทริปปัจจุบัน
    elif text == '/endtrip':
        supabase.table("trips").update({"status": "completed"}).eq("line_user_id", user_id).eq("status", "active").execute()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🏁 ปิดทริปเรียบร้อย! ระบบกำลังเตรียมสรุปยอด..."))

    # คำสั่งสรุปยอดทริปปัจจุบัน
    elif text == '/summary':
        trip_id = get_active_trip(user_id)
        if not trip_id:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ ไม่พบทริปที่กำลังใช้งาน"))
            return
        res = supabase.table("expenses").select("amount").eq("trip_id", trip_id).execute()
        total = sum(item['amount'] for item in res.data)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"📊 สรุปยอดทริปปัจจุบัน: {total:,.2f} บาท"))

    # รองรับการบันทึกด้วยการพิมพ์ (เช่น "50 ค่าข้าว")
    elif re.match(r'^\d+', text):
        trip_id = get_active_trip(user_id)
        if not trip_id: return
        parts = text.split(' ', 1)
        amount = float(parts[0].replace(',', ''))
        item_name = parts[1] if len(parts) > 1 else "ไม่ได้ระบุรายการ"
        supabase.table("expenses").insert({
            "trip_id": trip_id, 
            "line_user_id": user_id, 
            "amount": amount, 
            "item_name": item_name
        }).execute()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ บันทึก: {item_name} {amount:,.2f} บาท"))

# --- 4. ส่วนประมวลผลรูปภาพสลิป (Original Image Handler) ---
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
            
            # แก้ไข: ตรวจสอบให้มั่นใจว่า path ตรงกับเงื่อนไข Policy ที่ตั้งไว้ (LOWER extension)
            supabase.storage.from_('slips').upload(
                path=file_path,
                file=image_bytes,
                file_options={"content-type": "image/jpeg"}
            )

            # ดึง URL ของรูปที่อัปโหลด
            # แก้ไข: ใช้ .public_url เพื่อดึงค่า string ไปเก็บใน DB
            slip_url = supabase.storage.from_('slips').get_public_url(file_path).public_url

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
            print(f"Error: {e}") # สำหรับดู log ใน Render
    else:
        reply = "❓ ระบบไม่พบยอดเงินในสลิปใบนี้ กรุณาพิมพ์บันทึกเองนะครับ"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)