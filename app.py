import os
import re
import json
import logging
import threading
import pandas as pd
import io
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
# คง Comment เดิม: เพิ่ม FlexSendMessage เพื่อรองรับหน้าเมนูแบบปุ่มกด
from linebot.models import (
    MessageEvent, ImageMessage, TextMessage,
    TextSendMessage, FlexSendMessage
    # แก้ไขล่าสุด: ลบ FileSendMessage ออก เพราะไม่มีใน LINE Bot SDK Python
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
    res = supabase.table("trips").select("id, trip_name").eq("line_user_id", source_id).eq("status", "active").execute()
    return res.data[0] if res.data else None

# แก้ไขล่าสุด: ดึงชื่อจาก Profile กลุ่ม เพื่อให้รู้ว่าใครส่งแม้ไม่เป็นเพื่อนกับ Bot
def get_display_name(user_id, group_id=None):
    try:
        if group_id:
            profile = line_bot_api.get_group_member_profile(group_id, user_id)
        else:
            profile = line_bot_api.get_profile(user_id)
        return profile.display_name
    except Exception as e:
        logger.error(f"Profile Error: {e}")
        # แก้ไขล่าสุด: ถ้าดึงชื่อไม่ได้จริงๆ ให้ใช้ 4 หลักท้ายของ User ID เพื่อระบุตัวตน
        return f"สมาชิก({user_id[-4:]})"

def extract_amount(text):
    if not text:
        return None
    patterns = [
        r'(?:จำนวนเงิน|ยอดเงิน|Amount|Total|Net Amount)\s*[:-]?\s*([\d,]+\.\d{2})',
        r'([\d,]+\.\d{2})\s*(?:บาท|Baht|THB)'
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return float(match.group(1).replace(',', ''))
    return None

def create_menu_flex():
    return {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "🏔️ Trip Manager Menu", "weight": "bold", "color": "#FFFFFF", "size": "lg"}
            ],
            "backgroundColor": "#00B900"
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {"type": "button", "style": "primary", "color": "#1DB446", "action": {"type": "message", "label": "🚀 เริ่มทริปใหม่", "text": "ทริป"}},
                {"type": "button", "style": "secondary", "action": {"type": "message", "label": "📊 สรุปยอดรวม", "text": "ยอดรวม"}},
                {"type": "button", "style": "secondary", "action": {"type": "message", "label": "💰 หารค่าใช้จ่าย", "text": "หาร"}},
                {"type": "button", "style": "link", "color": "#FF5555", "action": {"type": "message", "label": " ปิดทริป", "text": "ปิดทริป"}}
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
    group_id = getattr(event.source, 'group_id', None)
    source_id = group_id or user_id
    current_state = user_state.get(user_id, {}).get("action")

    # แก้ไขล่าสุด: รายชื่อ Keyword การเงิน เพื่อใช้คัดกรองบทสนทนาทั่วไป
    FINANCE_KEYWORDS = ['จ่าย', 'ค่า', 'ราคา', 'บาท', 'baht', 'thb', 'โดนไป', 'จัดไป']

    # --- ส่วนจัดการปิดทริปและส่งออก Excel ---
    if text in ['/endtrip', 'ปิดทริป', 'ปิด']:
        trip_info = get_active_trip(event)
        if not trip_info:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ ไม่พบทริปที่เปิดอยู่"))
            return
        
        # แก้ไขล่าสุด: เข้าสู่สถานะรอจำนวนคนเพื่อสรุปยอด
        user_state[user_id] = {"action": "waiting_final_split", "trip_id": trip_info['id'], "trip_name": trip_info['trip_name']}
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🏁 ปิดทริป: {trip_info['trip_name']}\n กรุณาระบุจำนวนคนที่จะหารยอดรวมครับ: "))
        return

    if current_state == "waiting_final_split":
        if not text.isdigit():
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ กรุณาระบุเป็นตัวเลขจำนวนคนครับ"))
            return
        
        num_people = int(text)
        trip_id = user_state[user_id]["trip_id"]
        trip_name = user_state[user_id]["trip_name"]
        user_state.pop(user_id, None)

        # แก้ไขล่าสุด: ดึงข้อมูลมาสร้างสรุปและไฟล์ Excel
        res = supabase.table("expenses").select("*").eq("trip_id", trip_id).execute()
        if res.data:
            df = pd.DataFrame(res.data)
            total = df['amount'].sum()
            avg = total / num_people

            # สร้าง Excel ใน Memory
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df[['item_name', 'amount', 'created_at']].to_excel(writer, index=False, sheet_name='Summary')
            excel_data = output.getvalue()

            # Upload ขึ้น Supabase Storage (Bucket: reports)
            file_name = f"summary_{trip_id}.xlsx"
            supabase.storage.from_('reports').upload(path=file_name, file=excel_data, file_options={"content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"})
            public_url = supabase.storage.from_('reports').get_public_url(file_name)

            summary_msg = f"📊 สรุป {trip_name}\n💰 ยอดรวม: {total:,.2f} บาท\n👥 หาร {num_people} คน\n📉 ตกคนละ: {avg:,.2f} บาท"
            
            supabase.table("trips").update({"status": "completed"}).eq("id", trip_id).execute()

            line_bot_api.reply_message(event.reply_token, [
                TextSendMessage(text=summary_msg),
                TextSendMessage(text=f"📂 ดาวน์โหลดรายละเอียด Excel:\n{public_url}")
            ])
        return

    # --- ส่วนเริ่มทริป / เมนู (คงเดิม) ---
    if text.startswith('เริ่มทริป') or text.startswith('ทริป'):
        trip_name = text.replace('เริ่มทริป', '').replace('ทริป', '').strip()
        if trip_name:
            supabase.table("trips").update({"status": "completed"}).eq("line_user_id", source_id).eq("status", "active").execute()
            supabase.table("trips").insert({"line_user_id": source_id, "trip_name": trip_name, "status": "active"}).execute()
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🚀 เริ่มทริปใหม่: {trip_name} เรียบร้อย!"))
        else:
            user_state[user_id] = {"action": "waiting_trip_name"}
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✏️ กรุณาพิมพ์ชื่อทริปของคุณ: "))
        return

    # --- ส่วนบันทึกเงิน (ปรับปรุงการกรองเลขหัวเราะและดึงชื่อคนส่ง) ---
    money_match = re.search(r'\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\b', text)
    if money_match:
        val_str = money_match.group(0).replace(',', '')
        # แก้ไขล่าสุด: กรองเลขหัวเราะ 55, 555...
        if re.match(r'^5{2,}$', val_str):
            return

        # แก้ไขล่าสุด: เช็คว่าเลขอยู่ขอบประโยค หรือมี Keyword การเงิน
        is_at_edge = text.startswith(money_match.group(0)) or text.endswith(money_match.group(0))
        has_finance_keyword = any(k in text for k in FINANCE_KEYWORDS)

        if is_at_edge or has_finance_keyword:
            trip_data = get_active_trip(event)
            if not trip_data:
                return
            
            amount = float(val_str)
            if amount <= 0:
                return

            detail = text.replace(money_match.group(0), '').strip() or "ค่าใช้จ่าย"
            # แก้ไขล่าสุด: ส่ง group_id เข้าไปด้วยเพื่อดึงชื่อคนในกลุ่มได้ถูกต้อง
            sender_name = get_display_name(user_id, group_id)
            
            supabase.table("expenses").insert({
                "trip_id": trip_data['id'],
                "line_user_id": user_id,
                "amount": amount,
                "item_name": f"{detail} (โดย {sender_name})"
            }).execute()
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ บันทึกยอด {amount:,.2f} จากคุณ {sender_name} สำเร็จ!"))
        return

    # คำสั่งพื้นฐานอื่น ๆ (คงเดิม)
    if text in ['เมนู', '/menu', 'menu']:
        line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="Trip Menu", contents=create_menu_flex()))

# --- 4. ส่วนประมวลผลรูปภาพ (ปรับปรุงการดึงชื่อคนส่ง) ---
def process_image_async(reply_token, user_id, group_id, message_id, trip_id):
    try:
        message_content = line_bot_api.get_message_content(message_id)
        image_bytes = b''.join(message_content.iter_content())
        response = vision_client.text_detection(image=vision.Image(content=image_bytes))
        amount = extract_amount(response.text_annotations[0].description) if response.text_annotations else None
        if amount:
            sender_name = get_display_name(user_id, group_id)
            file_path = f"trips/{trip_id}/{message_id}.jpg"
            supabase.storage.from_('slips').upload(path=file_path, file=image_bytes, file_options={"content-type": "image/jpeg"})
            
            supabase.table("expenses").insert({
                "trip_id": trip_id,
                "line_user_id": user_id,
                "amount": amount,
                "slip_url": supabase.storage.from_('slips').get_public_url(file_path),
                "item_name": f"สลิป (โดย {sender_name})"
            }).execute()
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ บันทึกสลิป {amount:,.2f} บาท จากคุณ {sender_name} สำเร็จ!"))
    except Exception as e:
        logger.error(f"Image Error: {e}")

@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    trip_data = get_active_trip(event)
    if not trip_data:
        return
    group_id = getattr(event.source, 'group_id', None)
    threading.Thread(target=process_image_async, args=(event.reply_token, event.source.user_id, group_id, event.message.id, trip_data['id'])).start()

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))