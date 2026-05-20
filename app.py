import os
import re
import json
import logging
import threading
import requests
from datetime import datetime, timedelta
from flask import Flask, request, abort, render_template, send_from_directory, jsonify
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
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

supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_ANON_KEY"))

user_state = {}
SCHEDULES_FILE = "schedules.local.json"

# =================================================================
# [อัปเดตล่าสุด 2026-05-21]: ฟังก์ชันโหลดและบันทึก schedules
# =================================================================
def load_schedules():
    if os.path.exists(SCHEDULES_FILE):
        with open(SCHEDULES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_schedules(schedules):
    with open(SCHEDULES_FILE, "w", encoding="utf-8") as f:
        json.dump(schedules, f, ensure_ascii=False, indent=2)

# =================================================================
# [อัปเดตล่าสุด 2026-05-21]: API ดึงเวลาเปิดขายจาก URL
# =================================================================
@app.route("/api/event-time", methods=["GET"])
def get_event_time():
    url = request.args.get("url", "")
    if not url:
        return jsonify({"ok": False, "error": "ไม่มี URL"}), 400
    
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        
        # ดึงข้อความจากหน้าเว็บ
        html_text = resp.text
        
        # พยายามหาเวลาจาก meta tags หรือข้อความ
        time_patterns = [
            r'(\d{1,2})\s+(มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|กรกฎาคม|สิงหาคม|กันยายน|ตุลาคม|พฤศจิกายน|ธันวาคม)\s+(\d{3,4})',
            r'(\d{1,2})[-/](\d{1,2})[-/](\d{4})\s+(\d{1,2}):(\d{2})',
            r'(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})',
        ]
        
        matched_text = ""
        for pattern in time_patterns:
            match = re.search(pattern, html_text, re.IGNORECASE)
            if match:
                matched_text = match.group(0)
                break
        
        return jsonify({
            "ok": True,
            "isoLocal": "",
            "matchedText": matched_text
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# =================================================================
# [อัปเดตล่าสุด 2026-05-21]: API ส่ง LINE message
# =================================================================
@app.route("/api/line-push", methods=["POST"])
def line_push():
    data = request.json
    target_id = data.get("targetId")
    message = data.get("message")
    
    if not target_id or not message:
        return jsonify({"ok": False, "error": "ต้องระบุ targetId และ message"}), 400
    
    try:
        line_bot_api.push_message(target_id, TextSendMessage(text=message))
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"LINE push error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

# =================================================================
# [อัปเดตล่าสุด 2026-05-21]: API เช็คสถานะ Config
# =================================================================
@app.route("/api/config-status", methods=["GET"])
def config_status():
    return jsonify({
        "ok": True,
        "lineTokenConfigured": bool(os.getenv('LINE_CHANNEL_ACCESS_TOKEN')),
        "lineSecretConfigured": bool(os.getenv('LINE_CHANNEL_SECRET')),
        "lineUserConfigured": False  # ต้องให้ user ใส่เอง
    })

# =================================================================
# [อัปเดตล่าสุด 2026-05-21]: API จัดการ schedules (GET, POST, DELETE)
# =================================================================
@app.route("/api/schedules", methods=["GET", "POST"])
def handle_schedules():
    if request.method == "GET":
        schedules = load_schedules()
        return jsonify({"ok": True, "schedules": schedules})
    
    elif request.method == "POST":
        try:
            data = request.json
            schedules = load_schedules()
            
            # สร้าง ID ใหม่
            new_id = str(len(schedules) + 1)
            new_schedule = {
                "id": new_id,
                "targetId": data.get("targetId", ""),
                "buyerName": data.get("buyerName", ""),
                "seatCount": data.get("seatCount", ""),
                "zone": data.get("zone", ""),
                "name": data.get("name", ""),
                "url": data.get("url", ""),
                "totalPrice": data.get("totalPrice", ""),
                "saleTime": data.get("saleTime", ""),
                "site": data.get("site", ""),
                "reminders": [],
                "createdAt": datetime.now().isoformat()
            }
            
            schedules.append(new_schedule)
            save_schedules(schedules)
            return jsonify({"ok": True, "schedule": new_schedule})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/schedules/<schedule_id>", methods=["DELETE"])
def delete_schedule(schedule_id):
    try:
        schedules = load_schedules()
        schedules = [s for s in schedules if s.get("id") != schedule_id]
        save_schedules(schedules)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# =================================================================
# [อัปเดตล่าสุด 2026-05-21]: API เวลาเซิร์ฟเวอร์
# =================================================================
@app.route("/api/server-time", methods=["GET"])
def get_server_time():
    import time
    return jsonify({"ok": True, "serverTime": int(time.time() * 1000)})

# =================================================================
# [คงเดิม]: ฟังก์ชันบอตจัดการทริป
# =================================================================
def get_active_trip(event):
    user_id = event.source.user_id
    try:
        res = supabase.table("trips").select("*").eq("status", "active").eq("creator_id", user_id).execute()
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
# [คงเดิม]: เส้นทางหลัก
# =================================================================
@app.route("/")
def render_dashboard():
    return render_template("index.html")

@app.route("/static/<path:filename>")
def serve_static(filename):
    return send_from_directory("static", filename)

# =================================================================
# [คงเดิม]: LINE Webhook
# =================================================================
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
            import pandas as pd
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