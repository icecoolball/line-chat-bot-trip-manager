import os
import re
import json
import uuid
import logging
import threading
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
# คง Comment เดิม: เพิ่ม FlexSendMessage เพื่อรองรับหน้าเมนูแบบปุ่มกด
from linebot.models import (
    MessageEvent, ImageMessage, TextMessage,
    TextSendMessage, FlexSendMessage
)
from google.cloud import vision
from google.oauth2 import service_account
from supabase import create_client, Client

app = Flask(__name__)

# คง Comment เดิม: เพิ่ม logging เพื่อให้ debug ได้ง่ายขึ้น ดูใน Railway/Render logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

# แก้ไขล่าสุด: เพิ่ม user_state dict เพื่อเก็บสถานะว่า user กำลังรอ input อะไรอยู่
# key = user_id, value = dict เช่น {"action": "waiting_trip_name"} หรือ {"action": "waiting_split"}
# ใช้ in-memory เพราะ state จะหายเมื่อ server restart ซึ่งยอมรับได้สำหรับ use case นี้
user_state = {}

# --- 2. ฟังก์ชันช่วย (Helper Functions) ---

# คง Comment เดิม: ใช้ getattr ป้องกัน AttributeError กรณี source ไม่มี group_id
def get_active_trip(event):
    source_id = getattr(event.source, 'group_id', None) or event.source.user_id
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

# แก้ไขล่าสุด: ปรับ Flex Menu เปลี่ยนปุ่ม "หาร 2 คน" เป็น "หารค่าใช้จ่าย"
# และเปลี่ยน action เป็น /split เพื่อให้ระบบถามจำนวนคนแทนที่จะ fix เป็น 2
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
                {"type": "button", "style": "secondary", "action": {"type": "message", "label": "💰 หารค่าใช้จ่าย", "text": "/split"}},
                {"type": "button", "style": "link", "color": "#FF5555", "action": {"type": "message", "label": "🏁 ปิดทริป", "text": "/endtrip"}}
            ]
        }
    }

# คง Comment เดิม: แยก InvalidSignatureError ออกจาก Exception ทั่วไป
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    logger.info(f"[Webhook] Received body length: {len(body)}")
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        logger.error("[Webhook] Invalid signature — ตรวจสอบ LINE_CHANNEL_SECRET ใน Environment Variables")
        abort(400)
    except Exception as e:
        logger.error(f"[Webhook] Unexpected error: {e}")
        abort(500)
    return 'OK'

# คง Comment เดิม: เพิ่ม /health endpoint เพื่อป้องกัน Render Free Plan sleep
@app.route("/health", methods=['GET'])
def health():
    return 'OK', 200

# --- 3. ส่วนจัดการคำสั่งด้วยข้อความ (Comprehensive Text Handler) ---
@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    text = event.message.text.strip()
    user_id = event.source.user_id
    # คง Comment เดิม: ใช้ getattr ป้องกัน AttributeError เช่นเดียวกับ get_active_trip
    source_id = getattr(event.source, 'group_id', None) or user_id

    # คง Comment เดิม: เพิ่ม log ทุกครั้งที่รับคำสั่ง เพื่อ debug ใน Railway/Render logs
    logger.info(f"[Text] source_id={source_id}, user_id={user_id}, text='{text}'")

    # แก้ไขล่าสุด: เช็ค user_state ก่อนทุกอย่าง เพื่อรับ input ที่ระบบรอค้างไว้
    # เช่น รอชื่อทริป หรือรอจำนวนคนที่จะหาร
    current_state = user_state.get(user_id, {}).get("action")

    # --- รอรับชื่อทริปจาก user ---
    if current_state == "waiting_trip_name":
        trip_name = text or "ทริปใหม่"
        user_state.pop(user_id, None)  # เคลียร์ state
        supabase.table("trips").update({"status": "completed"}).eq("line_user_id", source_id).eq("status", "active").execute()
        supabase.table("trips").insert({"line_user_id": source_id, "trip_name": trip_name, "status": "active"}).execute()
        logger.info(f"[newtrip] Created trip '{trip_name}' for source_id={source_id}")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🚀 เริ่มทริปใหม่: {trip_name} เรียบร้อย!"))
        return

    # --- รอรับจำนวนคนที่จะหารจาก user ---
    if current_state == "waiting_split":
        user_state.pop(user_id, None)  # เคลียร์ state
        trip_id = user_state.get(user_id, {}).get("trip_id") or get_active_trip(event)
        try:
            num_people = int(text.strip())
            if num_people < 2:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ ต้องหารอย่างน้อย 2 คนขึ้นไปครับ"))
                return
            res = supabase.table("expenses").select("amount").eq("trip_id", trip_id).execute()
            total = sum(item['amount'] for item in res.data)
            per_person = total / num_people
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text=f"💰 หาร {num_people} คน\n📉 ยอดรวม: {total:,.2f}\n💳 จ่ายคนละ: {per_person:,.2f} บาท"
            ))
        except ValueError:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ กรุณาพิมพ์เป็นตัวเลขเท่านั้น เช่น 3"))
        return

    # คง Comment เดิม: ดักจับคำสั่ง "เมนู" เพื่อแสดง Flex Message
    if text in ['เมนู', '/menu', 'menu']:
        flex_menu = create_menu_flex()
        line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="Trip Menu", contents=flex_menu))
        return

    # คง Comment เดิม: เพิ่มคำสั่ง /help แสดงคำสั่งทั้งหมดเป็น text สำรอง
    if text in ['/help', 'help', 'ช่วยเหลือ']:
        help_text = (
            "📋 คำสั่งที่ใช้ได้:\n"
            "🚀 /newtrip — เริ่มทริปใหม่ (ระบบจะถามชื่อ)\n"
            "🏁 /endtrip — ปิดทริป\n"
            "📊 /summary — ดูยอดรวม\n"
            "💰 /split — หารค่าใช้จ่าย (ระบบจะถามจำนวนคน)\n"
            "💬 [จำนวน] [รายการ] — บันทึกค่าใช้จ่าย เช่น 50 ค่าข้าว\n"
            "📷 ส่งรูปสลิป — บันทึกจากสลิปอัตโนมัติ"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=help_text))
        return

    # แก้ไขล่าสุด: /newtrip ไม่รับชื่อทริปในบรรทัดเดียวกันอีกต่อไป
    # แต่จะตั้ง state "waiting_trip_name" แล้วถามชื่อทริปจาก user แทน
    if text.startswith('/newtrip'):
        user_state[user_id] = {"action": "waiting_trip_name"}
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✏️ กรุณาพิมพ์ชื่อทริปของคุณ:"))
        return

    # คำสั่งปิดทริปปัจจุบัน
    elif text == '/endtrip':
        user_state.pop(user_id, None)  # เคลียร์ state ค้างถ้ามี
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

    # แก้ไขล่าสุด: /split ไม่รับจำนวนคนในบรรทัดเดียวกันอีกต่อไป (ยกเว้นส่งมาพร้อมกัน เช่น /split 3)
    # ถ้าไม่มีตัวเลขต่อท้าย จะตั้ง state "waiting_split" แล้วถามจำนวนคนจาก user แทน
    elif text.startswith('/split'):
        trip_id = get_active_trip(event)
        if not trip_id:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ ไม่พบทริปที่กำลังใช้งาน"))
            return
        num_str = text.replace('/split', '').strip()
        if num_str.isdigit():
            # รองรับกรณีพิมพ์ /split 3 มาตรงๆ
            num_people = int(num_str)
            res = supabase.table("expenses").select("amount").eq("trip_id", trip_id).execute()
            total = sum(item['amount'] for item in res.data)
            per_person = total / num_people
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text=f"💰 หาร {num_people} คน\n📉 ยอดรวม: {total:,.2f}\n💳 จ่ายคนละ: {per_person:,.2f} บาท"
            ))
        else:
            # ไม่มีตัวเลข → ถามจำนวนคน
            user_state[user_id] = {"action": "waiting_split", "trip_id": trip_id}
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="👥 จะหารกี่คน? (พิมพ์ตัวเลข เช่น 3)"))

    # รองรับการบันทึกด้วยการพิมพ์ (เช่น "50 ค่าข้าว")
    elif re.match(r'^\d+', text):
        trip_id = get_active_trip(event)
        if not trip_id:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ ไม่พบทริปที่กำลังใช้งาน กรุณาพิมพ์ /newtrip ก่อน"))
            return
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

# คง Comment เดิม: แยก logic OCR และ Supabase ออกมาเป็น process_image_async
# และรันใน Thread แยก เพื่อป้องกัน Gunicorn worker timeout
def process_image_async(reply_token, user_id, message_id, trip_id):
    try:
        message_content = line_bot_api.get_message_content(message_id)
        image_bytes = b''.join(message_content.iter_content())
        image = vision.Image(content=image_bytes)
        response = vision_client.text_detection(image=image)
        texts = response.text_annotations

        if not texts:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="อ่านรูปไม่ได้ครับ"))
            return

        full_text = texts[0].description
        amount = extract_amount(full_text)

        if amount is not None:
            # C. อัปโหลดไป Storage (แยกโฟลเดอร์ตามทริป)
            file_path = f"trips/{trip_id}/{message_id}.jpg"
            supabase.storage.from_('slips').upload(
                path=file_path, file=image_bytes,
                file_options={"content-type": "image/jpeg"}
            )
            # คง Comment เดิม: supabase-py เวอร์ชันใหม่ get_public_url() คืนค่าเป็น str ตรงๆ
            slip_url = supabase.storage.from_('slips').get_public_url(file_path)

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
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ บันทึกสลิป {amount:,.2f} บาท สำเร็จ!"))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="❓ ไม่พบยอดเงินในสลิป"))

    except Exception as e:
        logger.error(f"[Image] Error saving slip: {e}")

@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    user_id = event.source.user_id
    trip_id = get_active_trip(event)

    if not trip_id:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ โปรดเริ่มทริปก่อนส่งสลิป"))
        return

    # คง Comment เดิม: ส่ง task ไปรันใน background thread แทนการรันตรงๆ
    t = threading.Thread(
        target=process_image_async,
        args=(event.reply_token, user_id, event.message.id, trip_id)
    )
    t.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
