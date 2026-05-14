import os
import re
import json
import uuid
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
# แก้ไขล่าสุด: คงการนำเข้า TextMessage และ TextSendMessage เพื่อรองรับคำสั่งเสียงและข้อความสรุปผล
from linebot.models import MessageEvent, ImageMessage, TextMessage, TextSendMessage
from google.cloud import vision
from google.oauth2 import service_account
from supabase import create_client, Client

app = Flask(__name__)

# --- 1. การตั้งค่าเริ่มต้น ---
# คง Comment เดิม: โหลด Config และตั้งค่าความปลอดภัย
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))

# Google Vision
creds_dict = json.loads(os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON'))
creds = service_account.Credentials.from_service_account_info(creds_dict)
vision_client = vision.ImageAnnotatorClient(credentials=creds)

# Supabase
supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_ANON_KEY"))

# --- 2. ฟังก์ชันช่วย (Helper Functions) ---

# แก้ไขล่าสุด: ปรับปรุงฟังก์ชันเช็คทริปให้รองรับ Group ID
# เพื่อให้สมาชิกทุกคนในกลุ่มบันทึกข้อมูลลงใน Trip ID เดียวกันที่ผูกกับกลุ่มนั้นๆ
def get_active_trip(event):
    # ตรวจสอบว่าเป็นเหตุการณ์จากกลุ่มหรือแชทส่วนตัวเพื่อใช้ ID ที่ถูกต้องในการค้นหาทริป
    source_id = event.source.group_id if event.source.type == 'group' else event.source.user_id
    res = supabase.table("trips").select("id").eq("line_user_id", source_id).eq("status", "active").execute()
    return res.data[0]['id'] if res.data else None

def extract_amount(text):
    if not text: return None
    # คง Comment เดิม: ดักจับรูปแบบยอดเงินทั่วไปในสลิปไทยและอังกฤษ
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

# --- 3. ส่วนจัดการคำสั่งด้วยข้อความ (Comprehensive Text Handler) ---
@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    text = event.message.text.strip()
    user_id = event.source.user_id
    # แก้ไขล่าสุด: ระบุ source_id เพื่อแยกทริปตามกลุ่มหรือรายบุคคล
    source_id = event.source.group_id if event.source.type == 'group' else user_id

    # คำสั่งสร้างทริปใหม่: /newtrip [ชื่อทริป]
    if text.startswith('/newtrip'):
        trip_name = text.replace('/newtrip', '').strip() or "ทริปใหม่"
        # แก้ไขล่าสุด: ปิดสถานะทริปเก่าของกลุ่ม/คนนี้ก่อนเริ่มทริปใหม่
        supabase.table("trips").update({"status": "completed"}).eq("line_user_id", source_id).eq("status", "active").execute()
        supabase.table("trips").insert({"line_user_id": source_id, "trip_name": trip_name, "status": "active"}).execute()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🚀 เริ่มทริปใหม่: {trip_name} เรียบร้อย!"))

    # คำสั่งปิดทริปปัจจุบัน
    elif text == '/endtrip':
        supabase.table("trips").update({"status": "completed"}).eq("line_user_id", source_id).eq("status", "active").execute()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🏁 ปิดทริปเรียบร้อย!"))

    # คำสั่งสรุปยอดทริปปัจจุบัน
    elif text == '/summary':
        trip_id = get_active_trip(event)
        if not trip_id:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ ไม่พบทริปที่กำลังใช้งาน"))
            return
        res = supabase.table("expenses").select("amount").eq("trip_id", trip_id).execute()
        total = sum(item['amount'] for item in res.data)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"📊 ยอดรวมทริป: {total:,.2f} บาท"))

    # เพิ่มเติมล่าสุด: ฟังก์ชันหารเงิน /split [จำนวนคน] เพื่อคำนวณยอดจ่ายต่อคน
    elif text.startswith('/split'):
        trip_id = get_active_trip(event)
        if not trip_id: return
        
        try:
            num_people = int(text.replace('/split', '').strip())
            res = supabase.table("expenses").select("amount").eq("trip_id", trip_id).execute()
            total = sum(item['amount'] for item in res.data)
            per_person = total / num_people if num_people > 0 else 0
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text=f"💰 สรุปการหารเงิน\n📉 ยอดรวม: {total:,.2f}\n👥 จำนวน: {num_people} คน\n💳 จ่ายคนละ: {per_person:,.2f} บาท"
            ))
        except ValueError:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ กรุณาระบุจำนวนคนเป็นตัวเลข เช่น /split 5"))

    # คง Comment เดิม: รองรับการบันทึกด้วยการพิมพ์ (เช่น "50 ค่าข้าว")
    elif re.match(r'^\d+', text):
        trip_id = get_active_trip(event)
        if not trip_id: return
        parts = text.split(' ', 1)
        amount = float(parts[0].replace(',', ''))
        item_name = parts[1] if len(parts) > 1 else "ไม่ได้ระบุรายการ"
        
        # แก้ไขล่าสุด: ดึงชื่อ Display Name เพื่อระบุตัวตนคนบันทึกในกลุ่ม
        try:
            profile = line_bot_api.get_profile(user_id)
            display_name = profile.display_name
        except:
            display_name = "User"

        supabase.table("expenses").insert({
            "trip_id": trip_id, 
            "line_user_id": user_id, 
            "amount": amount, 
            "item_name": f"{item_name} (บันทึกโดย {display_name})"
        }).execute()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ บันทึก: {item_name} {amount:,.2f} บาท เรียบร้อย!"))

# --- 4. ส่วนประมวลผลรูปภาพสลิป (Original Image Handler) ---
@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    user_id = event.source.user_id
    # แก้ไขล่าสุด: ใช้ฟังก์ชันรวมที่รองรับ Group เพื่อหาทริปที่ถูกต้อง
    trip_id = get_active_trip(event)
    
    if not trip_id:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ ไม่พบทริปที่กำลังใช้งาน พิมพ์ /newtrip เพื่อเริ่มทริปก่อนนะครับ"))
        return

    # B. คง Comment เดิม: ดึงรูปภาพจาก LINE และทำ OCR ด้วย Google Vision
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
            # C. แก้ไขล่าสุด: อัปโหลดรูปไปที่ Storage โดยแยกโฟลเดอร์ตาม trip_id
            # เพื่อให้สอดคล้องกับ Policy ที่ตั้งไว้ใน Storage
            file_path = f"trips/{trip_id}/{event.message.id}.jpg"
            
            supabase.storage.from_('slips').upload(
                path=file_path,
                file=image_bytes,
                file_options={"content-type": "image/jpeg"}
            )

            # แก้ไขล่าสุด: ใช้ .public_url เพื่อเก็บลิงก์ที่เข้าถึงได้ใน Database
            slip_url = supabase.storage.from_('slips').get_public_url(file_path).public_url

            # ดึงชื่อคนส่งสลิปเพื่อบันทึกลงในรายการ
            try:
                profile = line_bot_api.get_profile(user_id)
                user_name = profile.display_name
            except:
                user_name = "Anonymous"

            # D. บันทึกข้อมูลลงตาราง expenses ให้ครบถ้วนตาม Schema
            data = {
                "trip_id": trip_id,
                "line_user_id": user_id,
                "amount": amount,
                "slip_url": slip_url,
                "raw_ocr_data": {"full_text": full_text},
                "item_name": f"สลิปจาก {user_name}"
            }
            
            supabase.table("expenses").insert(data).execute()
            
            reply = f"✅ บันทึกสลิปสำเร็จ!\n👤 ผู้จ่าย: {user_name}\n💰 ยอดเงิน: {amount:,.2f} บาท"
            
        except Exception as e:
            reply = f"⚠️ พบยอดเงิน {amount:,.2f} บาท แต่บันทึกลงระบบไม่ได้"
            print(f"Error: {e}") 
    else:
        reply = "❓ ระบบไม่พบยอดเงินในสลิปใบนี้ กรุณาพิมพ์บันทึกเองนะครับ"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)