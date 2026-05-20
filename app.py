import os
import re
import json
import logging
import threading
import pandas as pd
import io
from flask import Flask, request, abort, render_template, send_from_directory, jsonify
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

# --- 1. การตั้งค่าเริ่มต้น ---\nline_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))

# Google Vision
creds_dict = json.loads(os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON'))
creds = service_account.Credentials.from_service_account_info(creds_dict)
vision_client = vision.ImageAnnotatorClient(credentials=creds)

# Supabase
supabase: Client = create_client(os.getenv(\"SUPABASE_URL\"), os.getenv(\"SUPABASE_ANON_KEY\"))

# แก้ไขล่าสุด: เพิ่ม user_state เพื่อคุมลำดับการกรอกข้อมูล
user_state = {}

# แก้ไขล่าสุด: กำหนดพาธไฟล์ของตารางตั้งจองตั๋วภายในเครื่องคอมพิวเตอร์
SCHEDULES_FILE = "schedules.local.json"

# --- 2. ฟังก์ชันช่วย (Helper Functions) ของบอตทริปเดิม [คงเดิมทุกประการ] ---
def get_active_trip(event):
    group_id = getattr(event.source, 'group_id', None)
    user_id = event.source.user_id
    target_id = group_id if group_id else user_id
    try:
        res = supabase.table("trips").select("*").eq("status", "active").or_(f"line_group_id.eq.{target_id},creator_id.eq.{target_id}").execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"Get Active Trip Error: {e}")
        return None

def get_display_name(user_id, group_id=None):
    try:
        if group_id:
            profile = line_bot_api.get_group_member_profile(group_id, user_id)
        else:
            profile = line_bot_api.get_profile(user_id)
        return profile.display_name
    except Exception as e:
        logger.error(f"Get Profile Error: {e}")
        return user_id[:8]

def extract_amount(text):
    clean_text = text.replace(',', '')
    match = re.search(r'(?:โอนเงินสำเร็จ|จำนวนเงิน|ยอดโอน|Amount)[:\s]*([\d.]+)', clean_text, re.IGNORECASE)
    if match: return float(match.group(1))
    amounts = [float(x) for x in re.findall(r'\b\d+\.\d{2}\b', clean_text)]
    return max(amounts) if amounts else None


# =================================================================
# [อัปเดตล่าสุด 2026-05-20]: เพิ่ม Endpoint ให้บริการหน้าบ้าน Ticket Dashboard
# =================================================================

@app.route("/")
def render_dashboard():
    # แก้ไขล่าสุด: ส่งหน้าเว็บควบคุมหลักออกไปแสดงผลที่พอร์ตหลักของ Flask
    return render_template("index.html")

@app.route("/static/<path:filename>")
def serve_static(filename):
    # แก้ไขล่าสุด: ส่งไฟล์ตัวเสริมระบบเช่น CSS และ JS จากโฟลเดอร์ static
    return send_from_directory("static", filename)

@app.route("/api/server-time", methods=["GET"])
def get_server_time():
    # แก้ไขล่าสุด: ดึงข้อมูลเวลาปัจจุบันของเครื่องเซิร์ฟเวอร์แบบมิลลิวินาทีสำหรับตัวนับถอยหลังหน้าบ้าน
    import time
    return jsonify({"ok": True, "serverTime": int(time.time() * 1000)})

@app.route("/api/schedules", methods=["GET", "POST"])
def handle_schedules():
    # แก้ไขล่าสุด: ระบบดึงและจัดเก็บค่าประวัติตารางนับถอยหลังจองตั๋วลงเครื่อง (Local JSON)
    if request.method == "POST":
        try:
            data = request.json
            with open(SCHEDULES_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
    else:
        if os.path.exists(SCHEDULES_FILE):
            with open(SCHEDULES_FILE, "r", encoding="utf-8") as f:
                return jsonify(json.load(f))
        return jsonify([])


# --- 3. LINE Webhook และฟังก์ชันบอตจัดการทริปเดิม [คงเดิมทุกประการ] ---
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    logger.info("Request body: " + body)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    text = event.message.text.strip()
    user_id = event.source.user_id
    group_id = getattr(event.source, 'group_id', None)
    reply_token = event.reply_token

    if text.startswith("สร้างทริป "):
        trip_name = text.replace("สร้างทริป ", "").strip()
        target_id = group_id if group_id else user_id
        try:
            supabase.table("trips").update({"status": "closed"}).or_(f"line_group_id.eq.{target_id},creator_id.eq.{target_id}").execute()
            supabase.table("trips").insert({
                "title": trip_name, "status": "active",
                "line_group_id": group_id if group_id else None, "creator_id": user_id
            }).execute()
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"🚀 สร้างทริป \"{trip_name}\" สำเร็จแล้ว! พร้อมบันทึกสลิปและเงินหารในกลุ่มนี้ทันที"))
        except Exception as e:
            logger.error(f"Create Trip Error: {e}")
            line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ เกิดข้อผิดพลาดในระบบฐานข้อมูล ไม่สามารถสร้างทริปได้"))
        return

    trip_data = get_active_trip(event)
    if not trip_data: return

    if text == "สรุปค่าใช้จ่าย":
        try:
            res = supabase.table("expenses").select("*").eq("trip_id", trip_data["id"]).execute()
            df = pd.DataFrame(res.data)
            if df.empty:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="ℹ️ ยังไม่มีค่าใช้จ่ายบันทึกในทริปนี้"))
                return
            df['display_name'] = df['line_user_id'].apply(lambda uid: get_display_name(uid, group_id))
            summary_df = df.groupby('display_name')['amount'].sum().reset_index()
            total_amount = df['amount'].sum()
            num_people = df['line_user_id'].nunique()
            per_person = total_amount / num_people if num_people > 0 else 0
            msg = f"📊 สรุปประวัติค่าใช้จ่ายทริป: {trip_data['title']}\n"
            for _, row in summary_df.iterrows():
                msg += f"• {row['display_name']}: {row['amount']:,.2f} บาท\n"
            msg += f"\n💰 ยอดรวมทั้งหมด: {total_amount:,.2f} บาท\n👥 สมาชิกทั้งหมด: {num_people} คน\n📉 เฉลี่ยคนละ: {per_person:,.2f} บาท"
            line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        except Exception as e:
            logger.error(f"Summary Error: {e}")
            line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ เกิดความล้มเหลวในการประมวลผล สรุปยอดเงินล้มเหลว"))

def process_slip(message_id, trip_id, user_id, group_id, reply_token):
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
                "trip_id": trip_id, "line_user_id": user_id, "amount": amount,
                "slip_url": supabase.storage.from_('slips').get_public_url(file_path),
                "item_name": f"สลิป (โดย {sender_name})"
            }).execute()
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ บันทึกสลิป {amount:,.2f} บาท จากคุณ {sender_name} สำเร็จ!"))
    except Exception as e:
        logger.error(f"Image Error: {e}")

@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    trip_data = get_active_trip(event)
    if not trip_data: return
    threading.Thread(target=process_slip, args=(event.message.id, trip_data["id"], event.source.user_id, getattr(event.source, 'group_id', None), event.reply_token)).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5177))
    app.run(host="0.0.0.0", port=port, debug=True)