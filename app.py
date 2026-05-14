import os
import re
import json
import uuid
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
# แก้ไขล่าสุด: เพิ่ม FlexSendMessage เพื่อรองรับหน้าเมนูแบบปุ่มกด
from linebot.models import (
    MessageEvent, ImageMessage, TextMessage, 
    TextSendMessage, FlexSendMessage
)
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

# แก้ไขล่าสุด: ปรับปรุงการหา Active Trip โดยรองรับทั้ง Group ID และ User ID
def get_active_trip(event):
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
        if match: return float(match.group(1).replace(',', ''))
    return None

# เพิ่มเติมล่าสุด: ฟังก์ชันสร้าง Flex Message Menu เพื่อใช้ในกลุ่ม
def create_menu_flex():
    return {
        "type": "bubble",
        "header": {
            "type": "box", "layout": "vertical", "contents": [
                {"type": "text", "text": "🏔️ Trip Manager Menu", "weight": "bold", "color": "#FFFFFF", "size": "lg"}
            ], "backgroundColor": "#00B900"
        },
        "body": {
            "type": "box", "layout": "vertical", "spacing": "md", "contents": [
                {"type": "button", "style": "primary", "color": "#1DB446", "action": {"type": "message", "label": "🚀 เริ่มทริปใหม่", "text": "/newtrip"}},
                {"type": "button", "style": "secondary", "action": {"type": "message", "label": "📊 สรุปยอดรวม", "text": "/summary"}},
                {"type": "button", "style": "secondary", "action": {"type": "message", "label": "💰 หาร 2 คน", "text": "/split 2"}},
                {"type": "button", "style": "link", "color": "#FF5555", "action": {"type": "message", "label": "🏁 ปิดทริป", "text": "/endtrip"}}
            ]
        }
    }

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
    source_id = event.source.group_id if event.source.type == 'group' else user_id

    # แก้ไขล่าสุด: ดักจับคำสั่ง "เมนู" เพื่อแสดง Flex Message
    if text in ['เมนู', '/menu', 'menu']:
        flex_menu = create_menu_flex()
        line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="Trip Menu", contents=flex_menu))
        return

    # คำสั่งสร้างทริปใหม่
    if text.startswith('/newtrip'):
        trip_name = text.replace('/newtrip', '').strip() or "ทริปใหม่"
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

    # เพิ่มเติม: คำสั่งหารเงิน /split
    elif text.startswith('/split'):
        trip_id = get_active_trip(event)
        if not trip_id: return
        try:
            num_people = int(text.replace('/split', '').strip())
            res = supabase.table("expenses").select("amount").eq("trip_id", trip_id).execute()
            total = sum(item['amount'] for item in res.data)
            per_person = total / num_people
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text=f"💰 หาร {num_people} คน\n📉 ยอดรวม: {total:,.2f}\n💳 จ่ายคนละ: {per_person:,.2f} บาท"
            ))
        except:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ ระบุจำนวนคนเป็นตัวเลข เช่น /split 3"))

    # รองรับการบันทึกด้วยการพิมพ์ (เช่น "50 ค่าข้าว")
    elif re.match(r'^\d+', text):
        trip_id = get_active_trip(event)
        if not trip_id: return
        parts = text.split(' ', 1)
        amount = float(parts[0].replace(',', ''))
        item_name = parts[1] if len(parts) > 1 else "ไม่ได้ระบุรายการ"
        
        try:
            profile = line_bot_api.get_profile(user_id)
            display_name = profile.display_name
        except:
            display_name = "User"

        supabase.table("expenses").insert({
            "trip_id": trip_id, "line_user_id": user_id, "amount": amount, 
            "item_name": f"{item_name} (โดย {display_name})"
        }).execute()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ บันทึก {amount:,.2f} บาท สำเร็จ!"))

# --- 4. ส่วนประมวลผลรูปภาพสลิป (Image Handler) ---
@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    user_id = event.source.user_id
    trip_id = get_active_trip(event)
    
    if not trip_id:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ โปรดเริ่มทริปก่อนส่งสลิป"))
        return

    # B. ดึงรูปภาพและทำ OCR
    message_content = line_bot_api.get_message_content(event.message.id)
    image_bytes = b''.join(message_content.iter_content())
    image = vision.Image(content=image_bytes)
    response = vision_client.text_detection(image=image)
    texts = response.text_annotations
    
    if not texts:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="อ่านรูปไม่ได้ครับ"))
        return

    full_text = texts[0].description
    amount = extract_amount(full_text)

    if amount is not None:
        try:
            # C. อัปโหลดไป Storage (แยกโฟลเดอร์ตามทริป)
            file_path = f"trips/{trip_id}/{event.message.id}.jpg"
            supabase.storage.from_('slips').upload(path=file_path, file=image_bytes, file_options={"content-type": "image/jpeg"})
            slip_url = supabase.storage.from_('slips').get_public_url(file_path).public_url

            try:
                profile = line_bot_api.get_profile(user_id)
                user_name = profile.display_name
            except:
                user_name = "Anonymous"

            # D. บันทึกข้อมูลลงตาราง expenses
            data = {
                "trip_id": trip_id, "line_user_id": user_id, "amount": amount,
                "slip_url": slip_url, "raw_ocr_data": {"full_text": full_text},
                "item_name": f"สลิปจาก {user_name}"
            }
            supabase.table("expenses").insert(data).execute()
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ บันทึกสลิป {amount:,.2f} บาท สำเร็จ!"))
        except Exception as e:
            print(f"Error: {e}") 
    else:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❓ ไม่พบยอดเงินในสลิป"))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)