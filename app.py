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

# คง Comment เดิม: ปรับ Flex Menu เปลี่ยนปุ่ม "หาร 2 คน" เป็น "หารค่าใช้จ่าย"
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
        logger.error("[Webhook] Invalid signature")
        abort(400)
    except Exception as e:
        logger.error(f"[Webhook] Unexpected error: {e}")
        abort(500)
    return 'OK'

# คง Comment เดิม: เพิ่ม /health endpoint เพื่อป้องกัน Render Free Plan sleep
@app.route("/health", methods=['GET'])
def health():
    return 'OK', 200

# --- 3. ส่วนจัดการคำสั่งด้วยข้อความ ---
@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    text = event.message.text.strip()
    user_id = event.source.user_id
    source_id = getattr(event.source, 'group_id', None) or user_id

    logger.info(f"[Text] source_id={source_id}, user_id={user_id}, text='{text}'")
    current_state = user_state.get(user_id, {}).get("action")

    # --- รอรับชื่อทริป ---
    if current_state == "waiting_trip_name":
        trip_name = text or "ทริปใหม่"
        user_state.pop(user_id, None)
        supabase.table("trips").update({"status": "completed"}).eq("line_user_id", source_id).eq("status", "active").execute()
        supabase.table("trips").insert({"line_user_id": source_id, "trip_name": trip_name, "status": "active"}).execute()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🚀 เริ่มทริปใหม่: {trip_name} เรียบร้อย!"))
        return

    # --- รอรับจำนวนคนที่จะหาร ---
    if current_state == "waiting_split":
        user_state.pop(user_id, None)
        trip_id = user_state.get(user_id, {}).get("trip_id") or get_active_trip(event)
        try:
            num_people = int(text.strip())
            res = supabase.table("expenses").select("amount").eq("trip_id", trip_id).execute()
            total = sum(item['amount'] for item in res.data)
            per_person = total / num_people
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text=f"💰 หาร {num_people} คน\n📉 ยอดรวม: {total:,.2f}\n💳 จ่ายคนละ: {per_person:,.2f} บาท"
            ))
        except:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ กรุณาพิมพ์เป็นตัวเลขเท่านั้น"))
        return

    # คำสั่งพื้นฐาน
    if text in ['เมนู', '/menu', 'menu']:
        line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="Trip Menu", contents=create_menu_flex()))
        return

    # แก้ไขล่าสุด: ปรับปรุงคำแนะนำให้สอดคล้องกับการพิมพ์สลับที่ได้
    if text in ['/help', 'help', 'ช่วยเหลือ']:
        help_text = (
            "📋 วิธีใช้งาน:\n"
            "🚀 เริ่มทริป: 'เริ่มทริป' หรือ 'ทริป'\n"
            "📊 ดูยอดรวม: 'ยอดรวม', 'ยอด' หรือ 'รวม'\n"
            "🏁 ปิดทริป: 'ปิดทริป' หรือ 'ปิด'\n"
            "💰 หารเงิน: 'หาร' (ตามด้วยจำนวนคน)\n"
            "💬 บันทึกเงิน: พิมพ์สลับที่กันได้เลย เช่น\n"
            "   • '500 เบียร์ ไอซ์'\n"
            "   • 'ไอซ์ เบียร์ 500'\n"
            "📷 ส่งรูปสลิปเพื่อบันทึกอัตโนมัติ"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=help_text))
        return

    if text.startswith('/newtrip') or text in ['เริ่มทริป', 'ทริป']:
        user_state[user_id] = {"action": "waiting_trip_name"}
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✏️ กรุณาพิมพ์ชื่อทริปของคุณ:"))
        return

    elif text in ['/endtrip', 'ปิดทริป', 'ปิด']:
        user_state.pop(user_id, None)
        supabase.table("trips").update({"status": "completed"}).eq("line_user_id", source_id).eq("status", "active").execute()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🏁 ปิดทริปเรียบร้อย!"))

    elif text in ['/summary', 'ยอดรวม', 'ยอด', 'รวม']:
        trip_id = get_active_trip(event)
        if not trip_id:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ ไม่พบทริปที่กำลังใช้งาน"))
            return
        res = supabase.table("expenses").select("amount").eq("trip_id", trip_id).execute()
        total = sum(item['amount'] for item in res.data)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"📊 ยอดรวมทริป: {total:,.2f} บาท"))

    elif text.startswith('/split') or text in ['หาร']:
        trip_id = get_active_trip(event)
        num_str = text.replace('/split', '').strip()
        if num_str.isdigit():
            num_people = int(num_str)
            res = supabase.table("expenses").select("amount").eq("trip_id", trip_id).execute()
            total = sum(item['amount'] for item in res.data)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"💰 หาร {num_people} คน\n📉 ยอดรวม: {total:,.2f}\n💳 จ่ายคนละ: {total/num_people:,.2f} บาท"))
        else:
            user_state[user_id] = {"action": "waiting_split", "trip_id": trip_id}
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="👥 จะหารกี่คน? (พิมพ์ตัวเลข เช่น 3)"))

    # แก้ไขล่าสุด: Logic บันทึกค่าใช้จ่าย (รองรับสลับที่ ชื่อคน/รายการ/ราคา)
    elif re.search(r'\d+', text):
        trip_id = get_active_trip(event)
        if not trip_id:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ โปรดเริ่มทริปก่อน"))
            return
        
        amounts = re.findall(r'[\d,]+\.?\d*', text)
        if amounts:
            amount_str = amounts[0].replace(',', '')
            try:
                amount = float(amount_str)
                # ตัดเฉพาะตัวเลขยอดเงินที่เจอออกจากข้อความ ส่วนที่เหลือคือรายละเอียดทั้งหมด
                detail = text.replace(amounts[0], '').strip() or "ไม่ได้ระบุรายละเอียด"
                
                try:
                    profile = line_bot_api.get_profile(user_id)
                    sender_name = profile.display_name
                except:
                    sender_name = "User"

                supabase.table("expenses").insert({
                    "trip_id": trip_id, "line_user_id": user_id, "amount": amount,
                    "item_name": f"{detail} (โดย {sender_name})"
                }).execute()
                
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ บันทึก {amount:,.2f} บาท สำหรับ '{detail}' สำเร็จ!"))
            except ValueError:
                pass

# --- 4. ส่วนประมวลผลรูปภาพสลิป ---
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

        amount = extract_amount(texts[0].description)
        if amount:
            file_path = f"trips/{trip_id}/{message_id}.jpg"
            supabase.storage.from_('slips').upload(path=file_path, file=image_bytes, file_options={"content-type": "image/jpeg"})
            slip_url = supabase.storage.from_('slips').get_public_url(file_path)
            
            try:
                user_name = line_bot_api.get_profile(user_id).display_name
            except:
                user_name = "Anonymous"

            supabase.table("expenses").insert({
                "trip_id": trip_id, "line_user_id": user_id, "amount": amount,
                "slip_url": slip_url, "item_name": f"สลิปจาก {user_name}"
            }).execute()
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ บันทึกสลิป {amount:,.2f} บาท สำเร็จ!"))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="❓ ไม่พบยอดเงินในสลิป"))
    except Exception as e:
        logger.error(f"[Image] Error: {e}")

@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    trip_id = get_active_trip(event)
    if not trip_id:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ โปรดเริ่มทริปก่อน"))
        return
    threading.Thread(target=process_image_async, args=(event.reply_token, event.source.user_id, event.message.id, trip_id)).start()

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))