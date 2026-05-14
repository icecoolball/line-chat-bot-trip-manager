import os
import re
import json
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
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))

# Google Vision
creds_dict = json.loads(os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON'))
creds = service_account.Credentials.from_service_account_info(creds_dict)
vision_client = vision.ImageAnnotatorClient(credentials=creds)

# Supabase
supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_ANON_KEY"))

# แก้ไขล่าสุด: เพิ่ม user_state เพื่อคุมลำดับการกรอกข้อมูล
user_state = {}

# --- 2. ฟังก์ชันช่วย (Helper Functions) ---

def get_active_trip(event):
    source_id = getattr(event.source, 'group_id', None) or event.source.user_id
    res = supabase.table("trips").select("id").eq("line_user_id", source_id).eq("status", "active").execute()
    return res.data[0]['id'] if res.data else None

# แก้ไขล่าสุด: ฟังก์ชันดึงชื่อจริงจาก LINE เพื่อรองรับชื่อแปลกๆ หรือ Emoji
def get_display_name(user_id):
    try:
        profile = line_bot_api.get_profile(user_id)
        return profile.display_name
    except:
        return "Unknown User"

def extract_amount(text):
    if not text: return None
    patterns = [
        r'(?:จำนวนเงิน|ยอดเงิน|Amount|Total|Net Amount)\s*[:\-]?\s*([\d,]+\.\d{2})',
        r'([\d,]+\.\d{2})\s*(?:บาท|Baht|THB)'
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match: return float(match.group(1).replace(',', ''))
    return None

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
                {"type": "button", "style": "primary", "color": "#1DB446", "action": {"type": "message", "label": "🚀 เริ่มทริปใหม่", "text": "ทริป"}},
                {"type": "button", "style": "secondary", "action": {"type": "message", "label": "📊 สรุปยอดรวม", "text": "ยอดรวม"}},
                {"type": "button", "style": "secondary", "action": {"type": "message", "label": "💰 หารค่าใช้จ่าย", "text": "หาร"}},
                {"type": "button", "style": "link", "color": "#FF5555", "action": {"type": "message", "label": "🏁 ปิดทริป", "text": "ปิดทริป"}}
            ]
        }
    }

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@app.route("/health", methods=['GET'])
def health():
    return 'OK', 200

# --- 3. ส่วนจัดการคำสั่งด้วยข้อความ ---
@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    text = event.message.text.strip()
    user_id = event.source.user_id
    source_id = getattr(event.source, 'group_id', None) or user_id
    current_state = user_state.get(user_id, {}).get("action")

    # --- รอรับชื่อทริป (กรณีถามหาชื่อ) ---
    if current_state == "waiting_trip_name":
        trip_name = text or "ทริปใหม่"
        user_state.pop(user_id, None)
        supabase.table("trips").update({"status": "completed"}).eq("line_user_id", source_id).eq("status", "active").execute()
        supabase.table("trips").insert({"line_user_id": source_id, "trip_name": trip_name, "status": "active"}).execute()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🚀 เริ่มทริปใหม่: {trip_name} เรียบร้อย!"))
        return

    # คำสั่งพื้นฐาน
    if text in ['เมนู', '/menu', 'menu']:
        line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="Trip Menu", contents=create_menu_flex()))
        return

    if text in ['/help', 'help', 'ช่วยเหลือ']:
        help_text = (
            "📋 วิธีใช้งาน:\n"
            "🚀 เริ่มทริป: 'เริ่มทริป' (หรือพิมพ์แค่ 'ทริป')\n"
            "📊 สรุปยอด: 'ยอดรวม', 'ยอด' หรือ 'รวม'\n"
            "🏁 ปิดทริป: 'ปิดทริป' หรือ 'ปิด'\n"
            "💰 หารเงิน: 'หาร' หรือ '/split'\n"
            "💬 บันทึกเงิน: 'เบียร์ 500' หรือ '500 ค่าข้าว'\n"
            "📷 ส่งรูปสลิปเพื่อบันทึกอัตโนมัติ"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=help_text))
        return

    # แก้ไขล่าสุด: รองรับการเริ่มทริปแบบ "ทริป [ชื่อ]" ในประโยคเดียว
    if text.startswith('เริ่มทริป') or text.startswith('ทริป') or text.startswith('/newtrip'):
        # ดึงชื่อทริปที่ต่อท้ายมา
        trip_name = text.replace('เริ่มทริป', '').replace('ทริป', '').replace('/newtrip', '').strip()
        if trip_name:
            supabase.table("trips").update({"status": "completed"}).eq("line_user_id", source_id).eq("status", "active").execute()
            supabase.table("trips").insert({"line_user_id": source_id, "trip_name": trip_name, "status": "active"}).execute()
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🚀 เริ่มทริปใหม่: {trip_name} เรียบร้อย!"))
        else:
            user_state[user_id] = {"action": "waiting_trip_name"}
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✏️ กรุณาพิมพ์ชื่อทริปของคุณ:"))
        return

    elif text in ['/endtrip', 'ปิดทริป', 'ปิด']:
        supabase.table("trips").update({"status": "completed"}).eq("line_user_id", source_id).eq("status", "active").execute()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🏁 ปิดทริปเรียบร้อย!"))
        return

    # สรุปยอดรวม (Summary)
    elif text in ['/summary', 'ยอดรวม', 'ยอด', 'รวม']:
        trip_id = get_active_trip(event)
        if not trip_id:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ ไม่พบทริปที่กำลังใช้งาน"))
            return
        res = supabase.table("expenses").select("amount, line_user_id").eq("trip_id", trip_id).execute()
        total = sum(e['amount'] for e in res.data)
        user_totals = {}
        for e in res.data:
            uid = e['line_user_id']
            user_totals[uid] = user_totals.get(uid, 0) + e['amount']
        summary_list = "".join([f"• {get_display_name(uid)}: {amt:,.2f} บาท\n" for uid, amt in user_totals.items()])
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"📊 ยอดรวมทริป: {total:,.2f} บาท\n\n💰 จ่ายแล้วโดย:\n{summary_list}"))
        return

    # แก้ไขล่าสุด: หารเงินรองรับคำว่า 'หาร' และ Net Settlement
    elif text.startswith('/split') or text.startswith('หาร') or (current_state == "waiting_split"):
        trip_id = get_active_trip(event)
        if not trip_id: return
        num_str = text.replace('/split', '').replace('หาร', '').strip()
        if not num_str.isdigit():
            user_state[user_id] = {"action": "waiting_split"}
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="👥 จะหารกี่คน? (พิมพ์ตัวเลข เช่น 3)"))
            return
        user_state.pop(user_id, None)
        num_people = int(num_str)
        res = supabase.table("expenses").select("amount, line_user_id").eq("trip_id", trip_id).execute()
        total = sum(e['amount'] for e in res.data)
        avg = total / num_people
        user_totals = {}
        for e in res.data:
            uid = e['line_user_id']
            user_totals[uid] = user_totals.get(uid, 0) + e['amount']
        result_text = f"📉 ยอดหารเฉลี่ย: {avg:,.2f} บาท/คน\n\n💵 ยอดสรุปสุทธิ (จ่ายเพิ่ม/รับคืน):\n"
        for uid, amt in user_totals.items():
            net = avg - amt
            status = f"จ่ายเพิ่ม {net:,.2f}" if net > 0 else (f"รับคืน {abs(net):,.2f}" if net < 0 else "ครบถ้วน")
            result_text += f"• {get_display_name(uid)}: {status}\n"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=result_text))
        return

    # บันทึกค่าใช้จ่าย (รองรับ Emoji/สลับที่)
    elif re.search(r'\d+', text):
        trip_id = get_active_trip(event)
        if not trip_id: return
        amounts = re.findall(r'[\d,]+\.?\d*', text)
        if amounts:
            amount = float(amounts[0].replace(',', ''))
            detail = text.replace(amounts[0], '').strip() or "ค่าใช้จ่าย"
            sender_name = get_display_name(user_id)
            supabase.table("expenses").insert({
                "trip_id": trip_id, "line_user_id": user_id, "amount": amount,
                "item_name": f"{detail} (โดย {sender_name})"
            }).execute()
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ บันทึกยอด {amount:,.2f} สำหรับ '{detail}' สำเร็จ!"))

# --- 4. ส่วนประมวลผลรูปภาพสลิป ---
def process_image_async(reply_token, user_id, message_id, trip_id):
    try:
        message_content = line_bot_api.get_message_content(message_id)
        image_bytes = b''.join(message_content.iter_content())
        response = vision_client.text_detection(image=vision.Image(content=image_bytes))
        amount = extract_amount(response.text_annotations[0].description) if response.text_annotations else None
        if amount:
            file_path = f"trips/{trip_id}/{message_id}.jpg"
            supabase.storage.from_('slips').upload(path=file_path, file=image_bytes, file_options={"content-type": "image/jpeg"})
            supabase.table("expenses").insert({
                "trip_id": trip_id, "line_user_id": user_id, "amount": amount,
                "slip_url": supabase.storage.from_('slips').get_public_url(file_path),
                "item_name": f"สลิป (โดย {get_display_name(user_id)})"
            }).execute()
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ บันทึกสลิป {amount:,.2f} บาท สำเร็จ!"))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="❓ ไม่พบยอดเงินในสลิป"))
    except Exception as e:
        logger.error(f"Image Error: {e}")

@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    trip_id = get_active_trip(event)
    if not trip_id: return
    threading.Thread(target=process_image_async, args=(event.reply_token, event.source.user_id, event.message.id, trip_id)).start()

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))