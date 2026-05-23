import os
import re
import json
import logging
import threading
import requests
# =================================================================
# [เพิ่มใหม่ 2026-05-22]: Import สำหรับ Export Excel
# =================================================================
import pandas as pd
from io import BytesIO
from datetime import datetime
from flask import Flask, request, abort, render_template, send_from_directory, jsonify
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, ImageMessage, TextMessage,
    TextSendMessage, QuickReply, QuickReplyButton, MessageAction
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
#SCHEDULES_FILE = "schedules.local.json"
#SHOWTIME_FILE = "showtime.local.json"

# =================================================================
# [แก้ไข 2026-05-23]: State Management พร้อม timeout 10 นาที
# สาเหตุ: Render free tier sleep/restart → state หาย → user ค้างโหมด
# แก้: ทุก state มี timestamp, เกิน 10 นาทีถือว่าหมดอายุ auto-clear
# =================================================================
STATE_TIMEOUT_SECONDS = 600  # 10 นาที

def set_state(user_id, data):
    """บันทึก state พร้อม timestamp"""
    data["_ts"] = datetime.now().timestamp()
    user_state[user_id] = data

def get_state(user_id):
    """ดึง state — ถ้าหมดอายุหรือไม่มีให้ return None"""
    s = user_state.get(user_id)
    if not s:
        return None
    if datetime.now().timestamp() - s.get("_ts", 0) > STATE_TIMEOUT_SECONDS:
        clear_state(user_id)
        return None
    return s

def clear_state(user_id):
    """ลบ state"""
    user_state.pop(user_id, None)

# =================================================================
# [อัปเดตล่าสุด 2026-05-22]: ฟังก์ชัน Showtime Management
# ประกาศ state สำหรับควบคุมการทำงาน: active (ปกติ) / showtime_mode (หยุดสลิป)
# =================================================================
def load_showtime():
    """ดึง showtime จาก Supabase แทนการอ่านไฟล์ JSON"""
    try:
        res = supabase.table("showtimes").select("*").execute()
        schedule = res.data if res.data else []
        return {"schedule": schedule, "last_updated": datetime.now().isoformat()}
    except Exception as e:
        logger.error(f"Load showtime error: {e}")
        return {"schedule": [], "last_updated": None}

def save_showtime(showtime_data):
    """บันทึก showtime ลง Supabase (ลบของเก่าทั้งหมดแล้วเพิ่มใหม่)"""
    try:
        # ลบข้อมูลเก่าทั้งหมด (id เป็น SERIAL เริ่มต้นที่ 1 ดังนั้น neq 0 จะลบทุกแถว)
        supabase.table("showtimes").delete().neq("id", 0).execute()
        
        # เพิ่มข้อมูลใหม่
        schedule = showtime_data.get("schedule", [])
        for item in schedule:
            supabase.table("showtimes").insert({
                "time": item.get("time", ""),
                "artist": item.get("artist", "")
            }).execute()
        return True
    except Exception as e:
        logger.error(f"Save showtime error: {e}")
        return False

def sort_showtime_by_time(schedule):
    """เรียง showtime: 09:00-23:59 มาก่อน, แล้ว 00:00-08:59"""
    def get_sort_key(item):
        time_str = item.get("time", "00:00").split('-')[0]
        try:
            h, m = map(int, time_str.split(':'))
            # ถ้า 00:00-08:59 ให้บวก 24 ชม. ไว้หลังสุด
            if 0 <= h < 9:
                return (1, h * 60 + m)   # group 1 (หลัง)
            else:
                return (0, h * 60 + m)   # group 0 (หน้า)
        except (ValueError, IndexError) as e:
            logger.warning(f"Sort showtime parse error: {time_str} - {e}")
            return (2, 0)
    return sorted(schedule, key=get_sort_key)

def format_showtime_message():
    """สร้าง message แสดง showtime ที่เก็บไว้ (เรียงตาม time) (คงเดิม)"""
    showtime = load_showtime()
    if not showtime.get("schedule"):
        return "ℹ️ ยังไม่มีข้อมูล Showtime"
    
    sorted_schedule = sort_showtime_by_time(showtime.get("schedule", []))
    msg = "📋 **ตารางการแสดง:**\n\n"
    for item in sorted_schedule:
        time = item.get("time", "-")
        artist = item.get("artist", "-")
        msg += f"⏱️ {time} | 🎤 {artist}\n"
    return msg
    
# =================================================================
# [อัปเดตล่าสุด 2026-05-22]: ฟังก์ชันโหลดและบันทึก schedules
# =================================================================
def load_schedules():
    """ดึง schedules จาก Supabase"""
    try:
        res = supabase.table("schedules").select("*").order("created_at", desc=True).execute()
        return res.data if res.data else []
    except Exception as e:
        logger.error(f"Load schedules error: {e}")
        return []

def save_schedules(schedules):
    """ไม่ใช้แล้ว - ใช้ insert/update/delete โดยตรงแทน"""
    pass

def add_schedule(data):
    """เพิ่ม schedule ใหม่"""
    try:
        new_schedule = {
            "target_id": data.get("targetId", ""),
            "buyer_name": data.get("buyerName", ""),
            "name": data.get("name", ""),
            "url": data.get("url", ""),
            "sale_time": data.get("saleTime", ""),
            "site": data.get("site", ""),
            "active": True
        }
        res = supabase.table("schedules").insert(new_schedule).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"Add schedule error: {e}")
        return None

def update_schedule_active(schedule_id, active):
    """อัพเดทสถานะ active"""
    try:
        supabase.table("schedules").update({"active": active}).eq("id", schedule_id).execute()
        return True
    except Exception as e:
        logger.error(f"Update schedule error: {e}")
        return False

def delete_schedule_by_id(schedule_id):
    """ลบ schedule"""
    try:
        supabase.table("schedules").delete().eq("id", schedule_id).execute()
        return True
    except Exception as e:
        logger.error(f"Delete schedule error: {e}")
        return False
# =================================================================
# [เพิ่มใหม่ 2026-05-22]: Currency Converter
# =================================================================
def get_exchange_rate(from_currency, to_currency):
    """ดึงอัตราแลกเปลี่ยนจาก API ฟรี"""
    try:
        url = f"https://api.exchangerate-api.com/v4/latest/{from_currency}"
        resp = requests.get(url, timeout=5)
        data = resp.json()
        rate = data.get("rates", {}).get(to_currency)
        return rate
    except Exception as e:
        logger.error(f"Get exchange rate error: {e}")
        return None
# =================================================================
# [แก้ไข 2026-05-22]: Export Excel Functions - แปลงเวลา UTC → ไทย
# =================================================================
def export_trip_to_excel(trip_id, trip_title):
    """สร้างไฟล์ Excel จากข้อมูลทริป (เวลาไทย UTC+7)"""
    try:
        from datetime import timedelta
        
        expenses = get_all_expenses(trip_id)
        if not expenses:
            return None, "ไม่มีข้อมูลค่าใช้จ่ายในทริปนี้"
        
        data = []
        for exp in expenses:
            user_name = get_display_name(exp['line_user_id'], None)
            created = exp.get('created_at', '')
            
            # [แก้ไข]: แปลงเวลา UTC → เวลาไทย (UTC+7)
            date_str = ''
            time_str = ''
            if created:
                try:
                    # ตัดเศษวินาทีและ timezone ออก แล้ว parse
                    dt_str = created.replace('Z', '+00:00')
                    dt = datetime.fromisoformat(dt_str)
                    # บวก 7 ชั่วโมงเป็นเวลาไทย
                    dt_thai = dt + timedelta(hours=7)
                    date_str = dt_thai.strftime('%Y-%m-%d')
                    time_str = dt_thai.strftime('%H:%M:%S')
                except Exception as e:
                    logger.warning(f"Parse datetime error: {created} - {e}")
                    date_str = created[:10]
                    time_str = created[11:19]
            
            data.append({
                "ชื่อทริป": trip_title,
                "วันที่": date_str,
                "เวลา": time_str,
                "ชื่อผู้จ่าย": user_name,
                "รายการ": exp.get('item_name', ''),
                "จำนวนเงิน": exp.get('amount', 0)
            })
        
        df = pd.DataFrame(data)
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Expenses')
        
        output.seek(0)
        return output, None
    except Exception as e:
        logger.error(f"Export Excel error: {e}")
        return None, str(e)
        
def upload_excel_to_supabase(file_buffer, filename):
    """อัพโหลดไฟล์ Excel ขึ้น Supabase Storage"""
    try:
        # อัพโหลดไฟล์ไปยัง bucket 'trip-exports'
        supabase.storage.from_("trip-exports").upload(
            path=filename,
            file=file_buffer.getvalue(),
            file_options={
                "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "upsert": "true"  # ถ้ามีไฟล์ชื่อเดียวกันให้ทับ
            }
        )
        
        # สร้าง public URL สำหรับดาวน์โหลด
        public_url = supabase.storage.from_("trip-exports").get_public_url(filename)
        return public_url, None
    except Exception as e:
        logger.error(f"Upload to Supabase Storage error: {e}")
        return None, str(e)
# =================================================================
# [อัปเดตล่าสุด 2026-05-21]: API ดึงเวลาเปิดขายจาก URL
# =================================================================
@app.route("/api/event-time", methods=["GET"])
def get_event_time():
    url = request.args.get("url", "")
    if not url:
        return jsonify({"ok": False, "error": "ไม่มี URL"}), 400
    
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=10)
        html_text = resp.text
        
        # [แก้ไข 2026-05-21]: ลบช่องว่างส่วนเกิน และแก้คำผิด (กัน ยายน -> กันยายน)
        time_patterns = [
            # Pattern 1: วันที่แบบภาษาไทย เช่น 15 กันยายน 2024
            r'(\d{1,2})\s+(มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|กรกฎาคม|สิงหาคม|กันยายน|ตุลาคม|พฤศจิกายน|ธันวาคม)\s+(\d{3,4})',
            
            # Pattern 2: วันที่แบบตัวเลข เช่น 15/09/2024 10:00 หรือ 15-09-2024 10:00
            r'(\d{1,2})[-/](\d{1,2})[-/](\d{4})\s+(\d{1,2}):(\d{2})',
        ]
        
        matched_text = ""
        for pattern in time_patterns:
            match = re.search(pattern, html_text, re.IGNORECASE)
            if match:
                matched_text = match.group(0)
                break
                
        return jsonify({"ok": True, "matchedText": matched_text})
    except Exception as e:
        logger.error(f"Get event time error: {e}")
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
        "lineUserConfigured": False
    })

# =================================================================
# [อัปเดตล่าสุด 2026-05-22]: API จัดการ schedules (GET, POST, DELETE)
# =================================================================
@app.route("/api/schedules", methods=["GET", "POST"])
def handle_schedules():
    if request.method == "GET":
        schedules = load_schedules()
        # แปลง field names ให้ตรงกับ frontend
        formatted = []
        for s in schedules:
            formatted.append({
                "id": str(s["id"]),
                "targetId": s.get("target_id", ""),
                "buyerName": s.get("buyer_name", ""),
                "name": s.get("name", ""),
                "url": s.get("url", ""),
                "saleTime": s.get("sale_time", ""),
                "site": s.get("site", ""),
                "active": s.get("active", True),
                "createdAt": s.get("created_at", "")
            })
        return jsonify({"ok": True, "schedules": formatted})
    
    elif request.method == "POST":
        try:
            data = request.json
            new_schedule = add_schedule(data)
            if new_schedule:
                # แปลง field names ให้ตรงกับ frontend
                formatted = {
                    "id": str(new_schedule["id"]),
                    "targetId": new_schedule.get("target_id", ""),
                    "buyerName": new_schedule.get("buyer_name", ""),
                    "name": new_schedule.get("name", ""),
                    "url": new_schedule.get("url", ""),
                    "saleTime": new_schedule.get("sale_time", ""),
                    "site": new_schedule.get("site", ""),
                    "active": new_schedule.get("active", True),
                    "createdAt": new_schedule.get("created_at", "")
                }
                return jsonify({"ok": True, "schedule": formatted})
            else:
                return jsonify({"ok": False, "error": "Failed to add schedule"}), 500
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/schedules/<schedule_id>", methods=["DELETE"])
def delete_schedule(schedule_id):
    try:
        success = delete_schedule_by_id(schedule_id)
        if success:
            return jsonify({"ok": True})
        else:
            return jsonify({"ok": False, "error": "Failed to delete"}), 500
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
# [อัปเดตล่าสุด 2026-05-21]: ฟังก์ชันหลักของบอต
# =================================================================

def get_active_trip(user_id, group_id=None):
    """ดึงทริปที่กำลัง active
    [Bug 4 fix]: ถ้าอยู่ในกลุ่ม → หาจาก line_group_id ก่อน
    ถ้าไม่มีกลุ่ม (DM) → fallback หาจาก creator_id
    เพิ่ม order+limit เพื่อป้องกัน active trip มากกว่า 1 (Bug 7)
    """
    try:
        # ถ้าอยู่ในกลุ่ม: หา trip ของกลุ่มนี้ก่อน (ทุกคนในกลุ่มใช้ได้)
        if group_id:
            res = supabase.table("trips").select("*") \
                .eq("status", "active") \
                .eq("line_group_id", group_id) \
                .order("created_at", desc=True) \
                .limit(1) \
                .execute()
            if res.data:
                return res.data[0]
        # DM หรือหาจากกลุ่มไม่เจอ: fallback ด้วย creator_id
        res = supabase.table("trips").select("*") \
            .eq("status", "active") \
            .eq("creator_id", user_id) \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"Get Active Trip Error: {e}")
        return None

def get_display_name(user_id, group_id=None):
    """ดึงชื่อผู้ใช้จาก LINE API"""
    try:
        if group_id:
            profile = line_bot_api.get_group_member_profile(group_id, user_id)
        else:
            profile = line_bot_api.get_profile(user_id)
        return profile.display_name
    except Exception as e:
        logger.error(f"Get display name error for user {user_id}: {e}")
        return user_id[:8]

def get_all_expenses(trip_id):
    """ดึงรายการค่าใช้จ่ายทั้งหมดของทริป และเรียงตาม ID จากน้อยไปมาก"""
    try:
        # [แก้ไข 2026-05-22]: ใช้ .order() ใน query แทนการ sort ใน Python
        res = supabase.table("expenses").select("*").eq("trip_id", trip_id).order("id", desc=False).execute()
        return res.data if res.data else []
    except Exception as e:
        logger.error(f"Get all expenses error: {e}")
        return []

def update_expense_amount(expense_id, new_amount):
    """แก้ไขจำนวนเงินของรายการค่าใช้จ่าย"""
    try:
        supabase.table("expenses").update({"amount": new_amount}).eq("id", expense_id).execute()
        return True
    except Exception as e:
        logger.error(f"Update expense error: {e}")
        return False

def send_menu(reply_token):
    """ส่งเมนูคำสั่งทั้งหมดแบบ Quick Reply"""
    quick_reply = QuickReply(items=[
        QuickReplyButton(action=MessageAction(label="📋 ID", text="id")),
        QuickReplyButton(action=MessageAction(label="🚀 สร้างทริป", text="ทริป ")),
        QuickReplyButton(action=MessageAction(label="💰 ยอดรวม", text="ยอด")),
        QuickReplyButton(action=MessageAction(label="🏁 จบทริป", text="จบทริป")),
        QuickReplyButton(action=MessageAction(label="✏️ แก้ไขยอด", text="edit")),
        QuickReplyButton(action=MessageAction(label="📅 Event", text="event")),
        QuickReplyButton(action=MessageAction(label="🛑 Stop Event", text="stop event")),
        QuickReplyButton(action=MessageAction(label="❓ เมนู", text="เมนู")),
        QuickReplyButton(action=MessageAction(label="❌ ยกเลิก", text="ยกเลิก")),
    ])
    
    msg = "📋 **รายการคำสั่งทั้งหมด**\n\n"
    msg += "🔑 **id** - แสดง User ID และ Group ID\n"
    msg += "🚀 **ทริป [ชื่อ]** - สร้างทริปใหม่\n"
    msg += "💰 **ยอด** - แสดงยอดรวมค่าใช้จ่าย\n"
    msg += "🏁 **จบทริป** - ปิดทริปและคำนวณหาร\n"
    msg += "✏️ **edit** - แก้ไขยอดเงิน (แสดงรายการเรียงตามยอดน้อยไปมาก พร้อม ID 4 หลัก)\n"
    msg += "✏️ **edit [ID] [จำนวน]** - แก้ไขทันที เช่น edit 0042 500\n"
    msg += "📅 **event** - แสดง Event ที่ตั้งค่าไว้\n"
    msg += "🛑 **stop event** - หยุดการแจ้งเตือน Event\n"
    msg += "📸 **ส่งรูปสลิป/บิล** - OCR อ่านยอดอัตโนมัติ\n"
    msg += "✏️ **พิมพ์ข้อความ** เช่น 'บอล ค่าเบียร์ 2000' - บันทึกค่าใช้จ่าย\n"
    msg += "❌ **ยกเลิก** - ออกจากโหมดแก้ไข\n\n"
    msg += "💡 **คำแนะนำ**: รายการจะเรียงตามยอดเงินจากน้อยไปมาก และแสดง ID 4 หลัก"
    
    line_bot_api.reply_message(
        reply_token,
        TextSendMessage(text=msg, quick_reply=quick_reply)
    )

# =================================================================
# [อัปเดตล่าสุด 2026-05-21]: ฟังก์ชันอ่าน Showtime จากรูป
# รองรับศิลปิน 3 ตำแหน่ง: บน/ซ้าย/ล่าง ของเวลา HH:MM-HH:MM
# =================================================================
def extract_showtime(text):
    """ดึงเวลา และ ชื่อศิลปินจาก OCR text
    รองรับ: บน/ซ้าย/ล่าง/ในบรรทัดเดียว + layout 3 column
    Return: list of {"time": "12:20-12:50", "artist": "SIFER"}
    """
    if not text:
        return []
    
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    showtime_list = []
    
    # Regex pattern เวลา: HH:MM-HH:MM หรือ HH.MM-HH.MM
    time_pattern = r'(\d{1,2}[:.]\d{2})\s*[-–]\s*(\d{1,2}[:.]\d{2})'
    
    for i, line in enumerate(lines):
        time_match = re.search(time_pattern, line)
        if not time_match:
            continue
        
        time_str = f"{time_match.group(1)}-{time_match.group(2)}"
        time_str = time_str.replace('.', ':')  # normalize . to :
        artist = None
        
        # Priority 1: ศิลปิน อยู่ในบรรทัดเดียวกัน (before หรือ after เวลา)
        # เช่น "SIFER 12:20-12:50" หรือ "12:20-12:50 SIFER"
        line_without_time = re.sub(time_pattern, '', line).strip()
        # ลบ emoji/symbol ที่อาจมากับ
        line_without_time = re.sub(r'^[⏱️🎤📋✏️🎵]+\s*', '', line_without_time).strip()
        if line_without_time and len(line_without_time) > 1:
            artist = line_without_time
        
        # Priority 2: ศิลปิน อยู่บรรทัดก่อนหน้า (ด้านบน) — ข้ามบรรทัดที่เป็นเวลา/emoji เท่านั้น
        if not artist:
            for j in range(i-1, max(i-3, -1), -1):  # ค้นหาย้อนหลัง 2-3 บรรทัด
                prev_line = lines[j].strip()
                # ข้ามเวลา emoji ตัวเลขเดี่ยว
                if re.search(time_pattern, prev_line) or re.match(r'^[\d\s\-:.\-–]+$', prev_line):
                    continue
                # ลบ emoji
                prev_line_clean = re.sub(r'^[⏱️🎤📋✏️🎵]+\s*', '', prev_line).strip()
                if prev_line_clean and len(prev_line_clean) > 1:
                    artist = prev_line_clean
                    break
        
        # Priority 3: ศิลปิน อยู่บรรทัดถัดไป (ด้านล่าง)
        if not artist:
            for j in range(i+1, min(i+3, len(lines))):  # ค้นหาข้างหน้า 2-3 บรรทัด
                next_line = lines[j].strip()
                # ข้ามเวลา emoji ตัวเลขเดี่ยว
                if re.search(time_pattern, next_line) or re.match(r'^[\d\s\-:.\-–]+$', next_line):
                    continue
                # ลบ emoji
                next_line_clean = re.sub(r'^[⏱️🎤📋✏️🎵]+\s*', '', next_line).strip()
                if next_line_clean and len(next_line_clean) > 1:
                    artist = next_line_clean
                    break
        
        # Priority 4: ถ้ายังหาไม่เจอ ใช้ "Unknown"
        if not artist:
            artist = "Unknown"
        
        showtime_list.append({"time": time_str, "artist": artist})
    
    logger.info(f"Showtime extracted ({len(showtime_list)} entries): {showtime_list}")
    return showtime_list

# =================================================================
# [อัปเดตล่าสุด 2026-05-21]: ปรับปรุง OCR extract_amount
#    🔧 หา label "จำนวน" ก่อนเสมอ แล้วดึงตัวเลขในบรรทัดนั้น/ถัดไป
#    🔧 ไม่ใช้ max() เพราะเลขที่รายการ/QR Code จะให้ค่าผิดพลาด
# =================================================================
def extract_amount(text):
    """ดึงจำนวนเงินจากสลิป — หา label จำนวน/Amount ก่อน แล้วดึงตัวเลขถัดไป"""
    if not text:
        return None

    lines = text.split('\n')

    # Priority 1: หาบรรทัดที่มี label "จำนวน" แล้วดึงตัวเลขในบรรทัดนั้นหรือบรรทัดถัดไป
    # รองรับ: "จำนวน:", "จำนวน :", "จำนวนเงิน", "Amount"
    amount_labels = ['จำนวน', 'amount']
    for i, line in enumerate(lines):
        line_lower = line.lower().strip()
        if any(label in line_lower for label in amount_labels):
            # ดึงตัวเลขจากบรรทัดเดียวกัน (เช่น "จำนวน: 45.00 บาท")
            amounts = re.findall(r'(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)', line)
            for a in amounts:
                try:
                    num = float(a.replace(',', ''))
                    if 1 <= num <= 1000000:
                        logger.info(f"OCR: found from label line '{line.strip()}' → {num}")
                        return num
                except:
                    continue
            # ถ้าไม่มีในบรรทัดเดียวกัน ดูบรรทัดถัดไป (เช่น label แยกกับตัวเลข)
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                amounts = re.findall(r'(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)', next_line)
                for a in amounts:
                    try:
                        num = float(a.replace(',', ''))
                        if 1 <= num <= 1000000:
                            logger.info(f"OCR: found from next line '{next_line}' → {num}")
                            return num
                    except:
                        continue

    # Priority 2: หาตัวเลขที่ตามด้วย "บาท" โดยตรง (เช่น "45.00 บาท")
    baht_matches = re.findall(r'(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)\s*บาท', text)
    for a in baht_matches:
        try:
            num = float(a.replace(',', ''))
            if 1 <= num <= 1000000:
                logger.info(f"OCR: found from บาท pattern → {num}")
                return num
        except:
            continue

    logger.info("OCR: no amount found")
    return None

def parse_expense_text(text):
    """แยกชื่อ รายการ และจำนวนเงิน เช่น 'บอล ค่าเบียร์ 2000 บาท'"""
    parts = text.split()
    if len(parts) >= 3:
        amount_match = re.search(r'(\d+(?:\.\d{2})?)', text)
        if amount_match:
            amount = float(amount_match.group(1))
            name = parts[0]
            item = ' '.join(parts[1:-1]) if len(parts) > 2 else "ค่าใช้จ่าย"
            return name, item, amount
    return None, None, None

def get_total_expenses(trip_id):
    """คำนวณยอดรวมและแยกตาม user"""
    try:
        res = supabase.table("expenses").select("*").eq("trip_id", trip_id).execute()
        expenses = res.data
        if not expenses:
            return 0, {}
        total = sum(e['amount'] for e in expenses)
        user_totals = {}
        for e in expenses:
            uid = e['line_user_id']
            user_totals[uid] = user_totals.get(uid, 0) + e['amount']
        return total, user_totals
    except Exception as e:
        logger.error(f"Get total expenses error: {e}")
        return 0, {}

def get_active_events():
    """ดึง event ที่กำลัง active จาก schedules"""
    schedules = load_schedules()
    active = [s for s in schedules if s.get('active', True)]
    return active

@app.route("/")
def render_dashboard():
    return render_template("index.html")

@app.route("/static/<path:filename>")
def serve_static(filename):
    return send_from_directory("static", filename)

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    # [Bug 2 fix]: แยก text_lower สำหรับ English keyword เท่านั้น
    # ภาษาไทยใช้ text ตรงๆ เพื่อป้องกัน .lower() ทำให้ match ผิดพลาด
    text = event.message.text.strip()          # ภาษาไทย: ใช้ต้นฉบับ
    text_lower = text.lower()                  # ภาษาอังกฤษ: lowercase
    user_id = event.source.user_id
    group_id = getattr(event.source, 'group_id', None)
    reply_token = event.reply_token

    # =============================================================
    # 0.1 Showtime: พิมพ์ showtime / editshowtime / save / update showtime
    # =============================================================
    if text_lower == "showtime":
        # แสดง showtime ล่าสุด และขอให้ส่งรูป
        msg = format_showtime_message()
        msg += "\n\n📸 ส่งรูป Showtime ใหม่เพื่ออัปเดต (bot จะหยุดรับสลิปชั่วคราว)"
        set_state(user_id, {"action": "showtime_mode"})
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return
    
    # save: บันทึก showtime (ถ้ามี temp) แล้ว resume สลิป + ออกจาก function
    if text_lower == "save":
        if get_state(user_id) and get_state(user_id).get("action") == "showtime_mode":
            # ถ้ามี showtime_temp ให้บันทึก
            if get_state(user_id).get("showtime_temp"):
                existing = load_showtime()
                sorted_schedule = sort_showtime_by_time(get_state(user_id)["showtime_temp"])
                existing["schedule"] = sorted_schedule
                existing["last_updated"] = datetime.now().isoformat()
                save_showtime(existing)
            
            # ออกจาก showtime_mode
            clear_state(user_id)
            line_bot_api.reply_message(reply_token, TextSendMessage(
                text="✅ บันทึก Showtime เสร็จ!\n\n"
                     "📸 ตอนนี้สลิปทำงานปกติแล้ว สามารถส่งรูปบิลเพื่อบันทึกยอดเงินได้"
            ))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่ได้อยู่ในโหมด Showtime"))
        return
    
    # editshowtime / update showtime: แก้ไข showtime
    if text_lower == "editshowtime" or text_lower == "update showtime":
        existing = load_showtime()
        if not existing.get("schedule"):
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ยังไม่มี Showtime ให้แก้ไข"))
            return
        msg = "✏️ แก้ไข Showtime (รองรับหลายบรรทัด)\n"
        msg += format_showtime_message()
        msg += "\n\n📸 ส่งรูป Showtime ใหม่ หรือ 📝 พิมพ์เพื่อเพิ่ม/แก้เฉพาะศิลปิน (ทีละหลายบรรทัด)"
        msg += "\n👉 เช่น:\n13:00-13:50 ROMANCE\n14:00-14:50 SWEET MULLET"
        set_state(user_id, {"action": "showtime_mode", "edit_mode": True})
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return
    
    # =============================================================
    # [Showtime fix]: รับข้อมูล showtime จากการพิมพ์ (รองรับหลายบรรทัด)
    # =============================================================
    if get_state(user_id) and get_state(user_id).get("action") == "showtime_mode" and \
       get_state(user_id).get("edit_mode"):
        lines = text.splitlines()
        updated = False
        existing = load_showtime()
        schedule = existing.get("schedule", [])
        time_pattern_input = r'^(\d{1,2}[:.]\d{2})\s*[-–]\s*(\d{1,2}[:.]\d{2})\s+(.+)$'
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            match = re.match(time_pattern_input, line)
            if match:
                time_input = f"{match.group(1)}-{match.group(2)}".replace('.', ':')
                artist_input = match.group(3).strip()
                found = False
                for item in schedule:
                    if item["time"] == time_input:
                        item["artist"] = artist_input
                        found = True
                        break
                if not found:
                    schedule.append({"time": time_input, "artist": artist_input})
                updated = True
        
        if updated:
            sorted_schedule = sort_showtime_by_time(schedule)
            existing["schedule"] = sorted_schedule
            existing["last_updated"] = datetime.now().isoformat()
            save_showtime(existing)
            msg = "✅ อัปเดต Showtime เสร็จ!\n\n"
            msg += format_showtime_message()
            msg += "\n\n📝 พิมพ์เพิ่มเติม หรือ 'save' เพื่อสิ้นสุดการแก้ไข"
            line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(
                text="⚠️ ไม่พบรูปแบบที่ถูกต้องในข้อความ\n\n"
                     "👉 ตัวอย่าง: 13:00-13:50 ROMANCE\n"
                     "👉 หรือส่งรูป Showtime ใหม่"
            ))
        return

    # =============================================================
    # 0. เมนู: เมนูหลัก หรือ เมนู showtime ถ้าอยู่ใน showtime_mode
    # =============================================================
    if text == "เมนู" or text_lower == "menu":
        # ถ้าอยู่ใน showtime_mode ให้แสดง help เฉพาะ showtime commands
        if get_state(user_id) and get_state(user_id).get("action") == "showtime_mode":
            msg = "📋 **Showtime Commands:**\n\n"
            msg += "📺 **showtime** - แสดง Showtime ล่าสุด\n"
            msg += "✏️ **update showtime** - แก้ไข Showtime (พิมพ์หรือส่งรูป)\n"
            msg += "✏️ **editshowtime** - แก้ไข Showtime\n"
            if get_state(user_id).get("edit_mode"):
                msg += "📝 **HH:MM-HH:MM ศิลปิน** - เพิ่ม/แก้ไข Showtime (หลายบรรทัดได้)\n"
            msg += "💾 **save** - บันทึก Showtime และออกจาก Function\n"
            msg += "❌ **ยกเลิก** - ออกจากโหมด Showtime\n"
            line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        else:
            # เมนูหลัก
            send_menu(reply_token)
        return

    # =============================================================
    # 0.5 Showtime Mode Guard: ถ้าอยู่ใน showtime_mode → บล็อก command อื่น
    # =============================================================
    if get_state(user_id) and get_state(user_id).get("action") == "showtime_mode":
        # ถ้า edit_mode=True ให้ตรวจว่าทุกบรรทัดที่ไม่ว่างตรง pattern หรือไม่
        if get_state(user_id).get("edit_mode"):
            lines = text.splitlines()
            all_valid = True
            time_pattern_input = r'^(\d{1,2}[:.]\d{2})\s*[-–]\s*(\d{1,2}[:.]\d{2})\s+(.+)$'
            for line in lines:
                line = line.strip()
                if line and not re.match(time_pattern_input, line):
                    all_valid = False
                    break
            if not all_valid:
                line_bot_api.reply_message(reply_token, TextSendMessage(
                    text="⚠️ รูปแบบไม่ถูกต้อง\n👉 ตัวอย่าง: 13:00-13:50 ROMANCE"
                ))
                return
            # ถ้าทุกบรรทัดถูกต้อง ให้ผ่านไป (เดี๋ยวส่วนรับ multi-line จะจัดการ)
        else:
            # อนุญาตแค่ showtime commands
            if not (text_lower == "save" or 
                    text_lower == "showtime" or 
                    text_lower == "editshowtime" or 
                    text_lower == "update showtime" or
                    text == "เมนู" or text_lower == "menu" or
                    text == "ยกเลิก" or text_lower == "cancel"):
                line_bot_api.reply_message(reply_token, TextSendMessage(
                    text="⏸️ ตอนนี้อยู่ในโหมด Showtime\n\n"
                         "อนุญาตแค่: showtime, editshowtime, update showtime, save, menu, ยกเลิก\n\n"
                         "พิมพ์ 'menu' เพื่อดูคำสั่ง Showtime"
                ))
                return

    if text == "ยกเลิก" or text_lower == "cancel":
        if get_state(user_id):
            if get_state(user_id).get("action") == "showtime_mode":
                clear_state(user_id)
                line_bot_api.reply_message(reply_token, TextSendMessage(text="✅ ออกจากโหมด Showtime เรียบร้อย"))
            else:
                clear_state(user_id)
                line_bot_api.reply_message(reply_token, TextSendMessage(text="✅ ยกเลิกโหมดแก้ไขเรียบร้อย"))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="ℹ️ ไม่มีโหมดแก้ไขที่กำลังทำงานอยู่"))
        return

    # =============================================================
    # 2. จัดการแก้ไขยอด (edit) - แสดง ID 4 หลัก เรียงตามยอดจากน้อยไปมาก
    # =============================================================
    if text_lower.startswith("edit"):
        trip = get_active_trip(user_id, group_id)
        if not trip:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่มีทริปที่กำลังทำงานอยู่"))
            return

        # [Bug 5 fix]: ตรวจ pattern "edit [ID] [amount]" ก่อน → ทำทันทีไม่ผ่าน state
        edit_parts = text_lower.split()
        if len(edit_parts) == 3 and edit_parts[0] == "edit":
            try:
                inline_id = int(edit_parts[1])
                inline_amount = float(edit_parts[2].replace(',', ''))
                if inline_amount > 0:
                    expenses = get_all_expenses(trip['id'])
                    selected = next((e for e in expenses if e['id'] == inline_id), None)
                    if selected:
                        if update_expense_amount(inline_id, inline_amount):
                            id_display = f"{inline_id:04d}"
                            line_bot_api.reply_message(reply_token, TextSendMessage(
                                text=f"✅ แก้ไขรายการ ID {id_display} ({selected['item_name'][:30]}) จาก {selected['amount']:,.2f} บาท เป็น {inline_amount:,.2f} บาท เรียบร้อย!"
                            ))
                        else:
                            line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ แก้ไขไม่สำเร็จ"))
                    else:
                        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"⚠️ ไม่พบรายการ ID {inline_id:04d}"))
                    return
            except (ValueError, IndexError):
                pass  # ถ้า parse ไม่ได้ ให้วิ่งต่อแสดง list ปกติ

        expenses = get_all_expenses(trip['id'])
        if not expenses:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่มีรายการค่าใช้จ่ายให้แก้ไข"))
            return
        
        # ล้าง state เดิมก่อน
        if get_state(user_id):
            clear_state(user_id)
        
        # สร้างข้อความแสดงรายการทั้งหมด (เรียงตามยอดจากน้อยไปมาก) - แสดง ID 4 หลัก
        msg = "✏️ เลือกรายการที่ต้องการแก้ไขยอดเงิน (พิมพ์ ID 4 หลัก):\n"
        msg += "=======================\n"
        for exp in expenses:  # ไม่ต้อง sort ซ้ำ เพราะ sort มาแล้ว
            # ตัดชื่อรายการให้สั้นลงเหลือ 35 ตัวอักษร
            short_name = exp['item_name'][:35] if len(exp['item_name']) > 35 else exp['item_name']
            # แสดง ID เป็น 4 หลัก (เติม 0 ข้างหน้า)
            id_display = f"{exp['id']:04d}"
            msg += f"ID {id_display}. {short_name}\n   💰 {exp['amount']:,.2f} บาท\n"
        
        msg += "\n=======================\n"
        msg += "👉 พิมพ์ 'edit 0042' เพื่อแก้ไขรายการ ID 42\n"
        msg += "👉 พิมพ์ 'edit 0042 500' เพื่อเปลี่ยน ID 42 เป็น 500 บาท\n"
        msg += "👉 พิมพ์ 'ยกเลิก' หรือ 'cancel' เพื่อออกจากโหมดแก้ไข"
        
        set_state(user_id, {"action": "edit_selection", "expenses": expenses})
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return
    
    # รับการเลือกแก้ไข (ใช้ ID จริง - รองรับทั้งแบบมีและไม่มี 0 ข้างหน้า)
    if get_state(user_id) and get_state(user_id).get("action") == "edit_selection":
        expenses = get_state(user_id)["expenses"]
        parts = text_lower.split()
        
        try:
            if len(parts) == 1:
                # รองรับ ID แบบมี 0 ข้างหน้า (0042) และไม่มี (42)
                expense_id = int(parts[0])
                selected = next((e for e in expenses if e['id'] == expense_id), None)
                
                if selected:
                    set_state(user_id, {
                        "action": "edit_amount",
                        "expense_id": selected['id'],
                        "expense_item": selected['item_name'],
                        "old_amount": selected['amount']
                    })
                    id_display = f"{selected['id']:04d}"
                    line_bot_api.reply_message(reply_token, TextSendMessage(
                        text=f"✏️ แก้ไขรายการ ID {id_display}: {selected['item_name'][:50]}\n💰 ยอดเดิม: {selected['amount']:,.2f} บาท\n\n👉 พิมพ์จำนวนเงินใหม่ (เช่น 500)\n👉 พิมพ์ 'ยกเลิก' หรือ 'cancel' เพื่อออก"
                    ))
                else:
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"⚠️ ไม่พบรายการ ID {expense_id}"))
                    clear_state(user_id)
            
            elif len(parts) >= 2:
                expense_id = int(parts[0])
                new_amount = float(parts[1].replace(',', ''))
                selected = next((e for e in expenses if e['id'] == expense_id), None)
                
                if selected and new_amount > 0:
                    if update_expense_amount(selected['id'], new_amount):
                        id_display = f"{selected['id']:04d}"
                        line_bot_api.reply_message(reply_token, TextSendMessage(
                            text=f"✅ แก้ไขรายการ ID {id_display} ({selected['item_name'][:30]}) จาก {selected['amount']:,.2f} บาท เป็น {new_amount:,.2f} บาท เรียบร้อย!"
                        ))
                    else:
                        line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ แก้ไขไม่สำเร็จ"))
                    clear_state(user_id)
                else:
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"⚠️ ไม่พบ ID {expense_id} หรือจำนวนเงินไม่ถูกต้อง"))
                    clear_state(user_id)
        except (ValueError, IndexError):
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาระบุ ID และจำนวนเงินให้ถูกต้อง (เช่น edit 0042 500)"))
            clear_state(user_id)
        return
    
    # รับจำนวนเงินใหม่หลังเลือก edit
    if get_state(user_id) and get_state(user_id).get("action") == "edit_amount":
        try:
            new_amount = float(text_lower.replace(',', ''))
            if new_amount <= 0:
                raise ValueError
            expense_id = get_state(user_id)["expense_id"]
            expense_item = get_state(user_id)["expense_item"]
            old_amount = get_state(user_id)["old_amount"]
            
            if update_expense_amount(expense_id, new_amount):
                id_display = f"{expense_id:04d}"
                line_bot_api.reply_message(reply_token, TextSendMessage(
                    text=f"✅ แก้ไขรายการ ID {id_display} ({expense_item[:30]}) จาก {old_amount:,.2f} บาท เป็น {new_amount:,.2f} บาท เรียบร้อย!"
                ))
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ แก้ไขไม่สำเร็จ"))
        except:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาพิมพ์จำนวนเงินเป็นตัวเลข (เช่น 500)"))
        clear_state(user_id)
        return

    # =============================================================
    # 3. พิมพ์ id - แสดง User ID ของคนพิมพ์ + Group ID
    #    [Bug 1 fix]: แสดง Group ID เพื่อให้ทุกคนในกลุ่มใช้ฟีเจอร์ทริปได้
    #    Group ID ใช้ร่วมกันได้ทั้งกลุ่ม ไม่ต้อง loop สมาชิกทีละคน
    # =============================================================
    if text == "id":
        msg = f"🔑 [LINE ID Info]\n\n👤 User ID (ของคุณ):\n{user_id}"
        if group_id:
            msg += f"\n\n👥 Group ID (ของกลุ่มนี้):\n{group_id}"
            msg += f"\n\n💡 นำ Group ID ไปตั้งค่าในหน้า Dashboard เพื่อส่งแจ้งเตือนเข้ากลุ่มได้เลย"
        else:
            msg += f"\n\nℹ️ แชทนี้เป็น DM (ไม่มี Group ID)"
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return

    # =============================================================
    # 4. พิมพ์ ทริป หรือ trip + ชื่อ - สร้างทริปใหม่
    # =============================================================
    if text.startswith("ทริป ") or text_lower.startswith("trip "):
        if text.startswith("ทริป "):
            trip_name = text[4:].strip()
        else:
            trip_name = text[5:].strip()
        
        if not trip_name:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาระบุชื่อทริป เช่น 'ทริป mujirock'"))
            return
        
        try:
            supabase.table("trips").update({"status": "closed"}).eq("creator_id", user_id).execute()
            supabase.table("trips").insert({
                "title": trip_name,
                "status": "active",
                "line_group_id": group_id,
                "creator_id": user_id
            }).execute()
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"🚀 เริ่มทริปใหม่: {trip_name} เรียบร้อย!"))
        except Exception as e:
            logger.error(f"Create trip error: {e}")
            line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ สร้างทริปไม่สำเร็จ กรุณาลองใหม่"))
        return

    # =============================================================
    # 5. พิมพ์ ยอด หรือ sum - แสดงยอดรวมล่าสุด
    # =============================================================
    if text == "ยอด" or text_lower == "sum":
        trip = get_active_trip(user_id, group_id)
        if not trip:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่มีทริปที่กำลังทำงานอยู่ พิมพ์ 'ทริป ชื่อทริป' เพื่อเริ่มทริป"))
            return
        
        total, user_totals = get_total_expenses(trip['id'])
        if total == 0:
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"💵 ยอดสรุปสุทธิ: 0.00 บาท\n\nยังไม่มีรายการค่าใช้จ่าย"))
            return
        
        msg = f"💵 ยอดสรุปสุทธิ: {total:,.2f} บาท\n"
        for uid, amt in user_totals.items():
            name = get_display_name(uid, group_id)
            msg += f"• {name}: {amt:,.2f} บาท\n"
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return

    # =============================================================
    # 6. พิมพ์ จบทริป หรือ end trip - ปิดทริปและคำนวณหาร (รองรับ Currency)
    # [อัปเดตล่าสุด 2026-05-22]: เพิ่มการแปลงสกุลเงิน เช่น จบทริป JPY
    # =============================================================
    if text.startswith("จบทริป") or text_lower.startswith("end trip"):
        trip = get_active_trip(user_id, group_id)
        if not trip:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่มีทริปที่กำลังทำงานอยู่"))
            return
    
        # ดึง currency code จาก command (เช่น "จบทริป JPY" หรือ "end trip USD")
        currency_code = "THB"
        if text.startswith("จบทริป"):
            parts = text.split()
            if len(parts) >= 2:
                currency_code = parts[1].upper()
        elif text_lower.startswith("end trip"):
            parts = text_lower.split()
            if len(parts) >= 3:
                currency_code = parts[2].upper()
    
        # เก็บ state ไว้รอจำนวนคน
        set_state(user_id, {
            "action": "end_trip",
            "trip_id": trip['id'],
            "trip_title": trip['title'],
            "currency_code": currency_code
        })
    
        if currency_code != "THB":
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"🏁 ปิดทริป: {trip['title']}\n💱 แปลงเป็นสกุล: {currency_code}\n\n👥 ระบุจำนวนคนที่จะหารครับ:"))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"🏁 ปิดทริป: {trip['title']}\n\n👥 ระบุจำนวนคนที่จะหารครับ:"))
        return
        
    # =============================================================
    # 6.1 รับจำนวนคนหลังจากจบทริป (รองรับ Currency)
    # [อัปเดตล่าสุด 2026-05-22]: เพิ่มการแปลงสกุลเงินและแสดงยอดคู่ขนาน
    # =============================================================
    if get_state(user_id) and get_state(user_id).get("action") == "end_trip":
        try:
            num_people = int(text)
            if num_people <= 0:
                raise ValueError
        except ValueError:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาระบุจำนวนคนเป็นตัวเลขที่มากกว่า 0"))
            return
        except Exception as e:
            logger.error(f"End trip input error: {e}")
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาระบุจำนวนคนเป็นตัวเลขที่มากกว่า 0"))
            return
        
        trip_id = get_state(user_id)["trip_id"]
        trip_title = get_state(user_id)["trip_title"]
        currency_code = get_state(user_id).get("currency_code", "THB")
        
        total, user_totals = get_total_expenses(trip_id)
        
        if total == 0:
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"🚀 ทริป: {trip_title}\n\n⚠️ ไม่มีรายการค่าใช้จ่ายให้หาร"))
            clear_state(user_id)
            return
        
        avg = total / num_people
        
        # ดึงอัตราแลกเปลี่ยนถ้าไม่ใช่ THB
        exchange_rate = 1
        if currency_code != "THB":
            rate = get_exchange_rate("THB", currency_code)
            if rate:
                exchange_rate = rate
            else:
                currency_code = "THB"  # Fallback ถ้า API ล่ม
        
        # สร้างข้อความสรุป
        msg = f"🚀 ทริป: {trip_title}\n"
        msg += f"👥 จำนวนคน: {num_people}\n\n"
        
        if currency_code != "THB" and exchange_rate != 1:
            msg += f"💱 อัตราแลกเปลี่ยน: 1 THB = {exchange_rate:.4f} {currency_code}\n\n"
            msg += f"📉 ยอดหารเฉลี่ย:\n"
            msg += f"   • {avg:,.2f} บาท/คน\n"
            msg += f"   • ≈ {avg * exchange_rate:,.2f} {currency_code}/คน\n\n"
        else:
            msg += f"📉 ยอดหารเฉลี่ย: {avg:,.2f} บาท/คน\n\n"
        
        msg += f"💵 ยอดสรุปสุทธิ (จ่ายเพิ่ม/รับคืน):\n"
        
        for uid, amt in user_totals.items():
            name = get_display_name(uid, group_id)
            diff = amt - avg
            
            if currency_code != "THB" and exchange_rate != 1:
                if diff > 0:
                    msg += f"• {name}: รับคืน {diff:,.2f} บาท (≈ {diff * exchange_rate:,.2f} {currency_code})\n"
                elif diff < 0:
                    msg += f"• {name}: จ่ายเพิ่ม {abs(diff):,.2f} บาท (≈ {abs(diff) * exchange_rate:,.2f} {currency_code})\n"
                else:
                    msg += f"• {name}: เรียบร้อยแล้ว\n"
            else:
                if diff > 0:
                    msg += f"• {name}: รับคืน {diff:,.2f} บาท\n"
                elif diff < 0:
                    msg += f"• {name}: จ่ายเพิ่ม {abs(diff):,.2f} บาท\n"
                else:
                    msg += f"• {name}: เรียบร้อยแล้ว\n"
        
        # ปิดทริปและบันทึกสกุลเงินที่ใช้
        try:
            supabase.table("trips").update({"status": "closed", "currency_code": currency_code}).eq("id", trip_id).execute()
        except Exception as e:
            logger.error(f"Close trip error: {e}")
            
        clear_state(user_id)
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return
    # =================================================================
    # [แก้ไข 2026-05-22]: Command Excel และ ประวัติ
    # =================================================================
    
    # =============================================================
    # Export Excel ทริปปัจจุบัน (พิมพ์แค่ "excel")
    # =============================================================
    if text_lower == "excel":
        trip = get_active_trip(user_id, group_id)
        if not trip:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่มีทริปที่กำลังทำงานอยู่"))
            return
        
        excel_buffer, error = export_trip_to_excel(trip['id'], trip['title'])
        if error:
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"❌ ไม่สามารถสร้าง Excel: {error}"))
            return
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{trip['title']}_{timestamp}.xlsx"
        public_url, upload_error = upload_excel_to_supabase(excel_buffer, filename)
        
        if upload_error:
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"❌ อัพโหลดล้มเหลว: {upload_error}"))
            return
        
        msg = f"✅ สร้างไฟล์ Excel สำเร็จ!\n\n"
        msg += f"📊 ทริป: {trip['title']}\n"
        msg += f"🔗 ลิงก์ดาวน์โหลด:\n{public_url}"
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return
    
    # =============================================================
    # ดูประวัติทริปทั้งหมด (Active + Closed)
    # =============================================================
    if text == "ประวัติ" or text_lower == "history":
        try:
            res = supabase.table("trips").select("*").order("created_at", desc=True).limit(10).execute()
            all_trips = res.data if res.data else []
            
            if not all_trips:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="ℹ️ ยังไม่มีประวัติทริป"))
                return
            
            msg = "📜 **ประวัติทริป (10 ทริปล่าสุด):**\n\n"
            for i, trip in enumerate(all_trips, 1):
                status_icon = "🟢" if trip['status'] == 'active' else "🔴"
                start_date = trip.get('created_at', '')[:10]
                end_date = trip.get('updated_at', '')[:10] if trip['status'] == 'closed' else "ยังไม่จบ"
                
                msg += f"{i}. {status_icon} {trip['title']}\n"
                msg += f"   📅 {start_date} → {end_date}\n"
                msg += f"   👉 พิมพ์: excel {i}\n\n"
            
            set_state(user_id, {"action": "export_history", "trips": all_trips})
            line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        except Exception as e:
            logger.error(f"History error: {e}")
            line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ ไม่สามารถดึงข้อมูลได้"))
        return
    
    # =============================================================
    # [แก้ไข 2026-05-22]: รับเลขเลือกทริปจากประวัติเพื่อ export
    # ปรับปรุง error handling และใช้ reply_message แทน push_message
    # =============================================================
    if get_state(user_id) and get_state(user_id).get("action") == "export_history":
        parts = text_lower.split()
        if len(parts) == 2 and parts[0] == "excel":
            try:
                choice = int(parts[1]) - 1
                trips = get_state(user_id)["trips"]
                
                if 0 <= choice < len(trips):
                    selected_trip = trips[choice]
                    
                    # สร้าง Excel
                    excel_buffer, error = export_trip_to_excel(selected_trip['id'], selected_trip['title'])
                    if error:
                        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"❌ ไม่สามารถสร้าง Excel: {error}"))
                        clear_state(user_id)
                        return
                    
                    # อัพโหลดขึ้น Supabase
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    filename = f"{selected_trip['title']}_{timestamp}.xlsx"
                    public_url, upload_error = upload_excel_to_supabase(excel_buffer, filename)
                    
                    if upload_error:
                        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"❌ อัพโหลดล้มเหลว: {upload_error}"))
                        clear_state(user_id)
                        return
                    
                    msg = f"✅ สร้างไฟล์ Excel สำเร็จ!\n\n"
                    msg += f"📊 ทริป: {selected_trip['title']}\n"
                    msg += f"🔗 ลิงก์ดาวน์โหลด:\n{public_url}"
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
                else:
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"⚠️ หมายเลขไม่ถูกต้อง (มี 1-{len(get_state(user_id)['trips'])})"))
                    return  # ไม่ลบ state ให้ user พิมพ์ใหม่ได้
            except ValueError:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาพิมพ์ตัวเลขเท่านั้น เช่น excel 1"))
                return  # ไม่ลบ state
            except Exception as e:
                logger.error(f"Export history error: {e}")
                line_bot_api.reply_message(reply_token, TextSendMessage(text=f"❌ เกิดข้อผิดพลาด: {str(e)}"))
            
            clear_state(user_id)
            return
        else:
            # ถ้าพิมพ์อย่างอื่นที่ไม่ใช่ "excel [เลข]" ให้ยกเลิก state
            clear_state(user_id) 
            
    # =============================================================
    # 7. บันทึกค่าใช้จ่ายด้วยข้อความ (ชื่อ รายการ จำนวนเงิน)
    # [แก้ไข 2026-05-22]: เพิ่ม "ประวัติ" และ "excel" ใน skip list
    # [Showtime fix]: ถ้าอยู่ใน showtime_mode ให้บล็อกการบันทึกยอด
    # =============================================================
    if get_state(user_id) and get_state(user_id).get("action") == "showtime_mode":
        # อยู่ในโหมด showtime → ไม่บันทึกยอด
        return

    if not text.startswith(("ทริป", "ยอด", "จบทริป", "เมนู", "ยกเลิก", "ประวัติ", "excel")) and \
       not text_lower.startswith(("trip", "sum", "end", "id", "event", "stop", "edit", "menu", "cancel", "history", "excel")):
        trip = get_active_trip(user_id, group_id)
        if trip:
            name, item, amount = parse_expense_text(event.message.text.strip())
            if amount and amount > 0:
                try:
                    sender_name = get_display_name(user_id, group_id)
                    supabase.table("expenses").insert({
                        "trip_id": trip['id'],
                        "line_user_id": user_id,
                        "amount": amount,
                        "item_name": item or "ค่าใช้จ่าย",
                        "slip_url": None
                    }).execute()
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ บันทึกยอด {amount:,.2f} บาท จากคุณ {sender_name} สำเร็จ!"))
                except Exception as e:
                    logger.error(f"Save expense error: {e}")
                return
                
    # =============================================================
    # 8. พิมพ์ event - แสดง event ปัจจุบัน
    # =============================================================
    if text_lower == "event":
        events = get_active_events()
        base_url = "https://line-chat-bot-trip-manager.onrender.com"
        
        if not events:
            msg = "🔍 ตรวจสอบรายชื่อคิว Event ปัจจุบัน...\n=======================\nℹ️ ไม่มีคิว Event ที่เปิดอยู่ (หรือทุกงานหมดอายุ/ถูกปิดแล้ว)\n-----------------------\n\n💻 ลิงก์ควบคุมแผงระบบ:\n" + base_url
        else:
            msg = "🔍 ตรวจสอบรายชื่อคิว Event ปัจจุบัน...\n=======================\n"
            for i, e in enumerate(events, 1):
                msg += f"{i}. งาน: {e.get('name', '-')}\n⏰ เวลาขาย: {e.get('saleTime', '-')}\n🔗 ลิงก์งาน: {e.get('url', '-')}\n-----------------------\n"
            msg += f"\n💻 ลิงก์ควบคุมแผงระบบ:\n{base_url}"
        
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return

    # =============================================================
    # 9. พิมพ์ stop event - แสดงรายการให้เลือกหยุด
    # =============================================================
    if text_lower == "stop event":
        events = get_active_events()
        if not events:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่มี Event ที่กำลังทำงานอยู่"))
            return
        
        set_state(user_id, {"action": "stop_event", "events": events})
        msg = "🚫 เลือกหมายเลข Event ที่คุณต้องการสั่งหยุดทำงาน (Stop):\n=======================\n"
        for i, e in enumerate(events, 1):
            msg += f"{i}. งาน: {e.get('name', '-')}\n🛑 (ID ย่อ: {e.get('id', '-')})\n-----------------------\n"
        msg += "👉 พิมพ์เฉพาะ [ตัวเลขลำดับ] เพื่อระบุเลือกปิดงานชิ้นนั้นได้เลยครับ"
        
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return

    # =============================================================
    # 9.1 รับเลข event ที่เลือกหยุด
    # =============================================================
    if get_state(user_id) and get_state(user_id).get("action") == "stop_event":
        try:
            choice = int(text_lower) - 1
            events = get_state(user_id)["events"]
            if 0 <= choice < len(events):
                selected = events[choice]
                schedules = load_schedules()
                for s in schedules:
                    if s.get('id') == selected.get('id'):
                        s['active'] = False
                        break
                save_schedules(schedules)
                line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ สั่งปิดงานเรียบร้อยแล้ว!\n🛑 สั่งหยุดภารกิจงาน: {selected.get('name', '-')}\nสถานะคิวเตือนถูกระงับถาวรเรียบร้อย"))
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ หมายเลขไม่ถูกต้อง กรุณาลองใหม่"))
        except ValueError as e:
            logger.warning(f"Stop event invalid input: {text_lower} - {e}")
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาพิมพ์หมายเลขเท่านั้น"))
        except Exception as e:
            logger.error(f"Stop event error: {e}")
            line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ เกิดข้อผิดพลาด กรุณาลองใหม่"))
        clear_state(user_id)
        return

# =============================================================
# 10. รองรับรูปภาพสลิป/บิล
# =============================================================
def process_slip(message_id, trip_id, user_id, group_id, reply_token):
    # [Showtime fix]: ตรวจ state showtime_mode → ถ้า active ให้ pause การประมวลสลิป
    if get_state(user_id) and get_state(user_id).get("action") == "showtime_mode":
        line_bot_api.reply_message(reply_token, TextSendMessage(
            text="⏸️ กำลังอยู่ในโหมด Showtime\n\n"
                 "พิมพ์ 'save' เพื่อบันทึก showtime และกลับมายังโหมดปกติ"
        ))
        return
    
    try:
        message_content = line_bot_api.get_message_content(message_id)
        image_bytes = b''.join(message_content.iter_content())
        response = vision_client.text_detection(image=vision.Image(content=image_bytes))
        text_detected = response.text_annotations[0].description if response.text_annotations else ""
        
        logger.info(f"OCR Text detected (first 500 chars): {text_detected[:500] if text_detected else 'None'}")
        
        amount = extract_amount(text_detected)
        
        if amount:
            sender_name = get_display_name(user_id, group_id)
            timestamp = datetime.now().strftime('%d/%m/%y %H:%M:%S')
            item_name = f"บิล {timestamp} (โดย {sender_name})"
            
            result = supabase.table("expenses").insert({
                "trip_id": trip_id,
                "line_user_id": user_id,
                "amount": amount,
                "slip_url": f"slip_{message_id}",
                "item_name": item_name
            }).execute()
            
            # ดึง ID ของรายการที่เพิ่งเพิ่ม
            new_id = result.data[0]['id'] if result.data else None
            
            success_msg = f"✅ บันทึกจำนวนเงิน {amount:,.2f} บาท จากคุณ {sender_name} สำเร็จ!"
            
            if new_id:
                id_display = f"{new_id:04d}"
                success_msg += f"\n\n✏️ หากยอดไม่ถูกต้อง พิมพ์: edit {id_display} {amount}"
            else:
                success_msg += f"\n\n✏️ หากยอดไม่ถูกต้อง พิมพ์: edit แล้วเลือก ID ที่ต้องการ"
            
            line_bot_api.reply_message(reply_token, TextSendMessage(text=success_msg))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(
                text="⚠️ ไม่พบจำนวนเงินในรูป หรือไม่ใช่สลิปการเงิน\n\n"
                     "📌 ลองบันทึกด้วยข้อความ เช่น 'บอล ค่าเหล้า 500'\n"
                     "✏️ หรือพิมพ์ 'edit' เพื่อแก้ไขภายหลัง"
            ))
    except Exception as e:
        logger.error(f"Process slip error: {e}")
        line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ ไม่สามารถอ่านรูปได้ กรุณาลองใหม่"))

@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    # [Bug 4 fix]: ส่ง group_id เข้า get_active_trip เพื่อให้ user ทุกคนในกลุ่มส่งสลิปได้
    user_id = event.source.user_id
    group_id = getattr(event.source, 'group_id', None)
    reply_token = event.reply_token
    
    # [Showtime fix]: ตรวจสอบ showtime_mode ก่อน
    if get_state(user_id) and get_state(user_id).get("action") == "showtime_mode":
        try:
            message_content = line_bot_api.get_message_content(event.message.id)
            image_bytes = b''.join(message_content.iter_content())
            response = vision_client.text_detection(image=vision.Image(content=image_bytes))
            text_detected = response.text_annotations[0].description if response.text_annotations else ""
            
            logger.info(f"Showtime OCR detected: {text_detected[:300]}")
            showtime_list = extract_showtime(text_detected)
            
            if showtime_list:
                if get_state(user_id).get("edit_mode"):
                    # [Showtime fix]: edit_mode → merge กับ existing schedule
                    existing = load_showtime()
                    schedule = existing.get("schedule", [])
                    
                    # merge: update หรือ append
                    for new_item in showtime_list:
                        found = False
                        for i, old_item in enumerate(schedule):
                            if old_item["time"] == new_item["time"]:
                                schedule[i] = new_item
                                found = True
                                break
                        if not found:
                            schedule.append(new_item)
                    
                    # [Fix 1]: Sort ก่อนบันทึก
                    sorted_schedule = sort_showtime_by_time(schedule)
                    existing["schedule"] = sorted_schedule
                    existing["last_updated"] = datetime.now().isoformat()
                    save_showtime(existing)
                    
                    msg = "✅ อัปเดต Showtime เสร็จ!\n\n"
                    msg += format_showtime_message()
                    msg += "\n\n📝 พิมพ์เพิ่มเติม หรือ 'save' เพื่อสิ้นสุดการแก้ไข"
                else:
                    # [Showtime fix]: normal mode → เก็บไว้ใน state ชั่วคราว รอ user พิมพ์ save
                    get_state(user_id)["showtime_temp"] = showtime_list
                    msg = "✅ อ่านข้อมูล Showtime สำเร็จ\n\n📋 **ตารางการแสดง:**\n\n"
                    for item in showtime_list:
                        msg += f"⏱️ {item['time']} | 🎤 {item['artist']}\n"
                    msg += "\n👉 พิมพ์ 'save' เพื่อบันทึก"
                
                line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(
                    text="⚠️ ไม่พบข้อมูล Showtime ในรูป\n"
                         "🔍 ตรวจสอบรูปอีกครั้งหรือพิมพ์ข้อมูลด้วยตนเอง"
                ))
        except Exception as e:
            logger.error(f"Showtime OCR error: {e}")
            line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ ไม่สามารถอ่านรูปได้"))
        return
    
    # ไม่ใช่ showtime_mode → process slip ปกติ
    trip = get_active_trip(user_id, group_id)
    if not trip:
        line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่มีทริปที่กำลังทำงานอยู่ พิมพ์ 'ทริป ชื่อทริป' เพื่อเริ่มทริป"))
        return
    threading.Thread(target=process_slip, args=(event.message.id, trip['id'], user_id, group_id, reply_token)).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5177))
    app.run(host="0.0.0.0", port=port, debug=True)