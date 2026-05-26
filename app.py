import os
import re
import json
import logging
import threading
import requests
import pandas as pd
from io import BytesIO
from datetime import datetime, timedelta, date
from flask import Flask, request, abort, render_template, send_from_directory, jsonify
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, ImageMessage, TextMessage,
    TextSendMessage, QuickReply, QuickReplyButton, MessageAction,
    FlexSendMessage, BubbleContainer, BoxComponent, TextComponent, ButtonComponent, URIAction
)
from google.cloud import vision
from google.oauth2 import service_account
from supabase import create_client, Client

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- 1. การตั้งค่าเริ่มต้น ---
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))

creds_dict = json.loads(os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON'))
creds = service_account.Credentials.from_service_account_info(creds_dict)
vision_client = vision.ImageAnnotatorClient(credentials=creds)

supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_ANON_KEY"))

user_state = {}
STATE_TIMEOUT_SECONDS = 600  # 10 นาที

def set_state(user_id, data):
    data["_ts"] = datetime.now().timestamp()
    user_state[user_id] = data

def get_state(user_id):
    s = user_state.get(user_id)
    if not s: return None
    # Showtime mode ไม่หมดอายุตามเวลา แต่หมดอายุตามวันที่
    if s.get("action") == "showtime_mode":
        end_date_str = s.get("end_date")
        if end_date_str:
            try:
                end_dt = datetime.strptime(end_date_str, "%Y-%m-%d")
                if datetime.now() > end_dt + timedelta(days=1):
                    clear_state(user_id)
                    return None
            except: pass
        return s
    
    if datetime.now().timestamp() - s.get("_ts", 0) > STATE_TIMEOUT_SECONDS:
        clear_state(user_id)
        return None
    return s

def clear_state(user_id):
    user_state.pop(user_id, None)

# =================================================================
# [NEW] Flex Message Builders
# =================================================================
def build_main_menu_flex():
    return FlexSendMessage(alt_text="เมนูคำสั่ง", contents=BubbleContainer(
        body=BoxComponent(layout='vertical', contents=[
            TextComponent(text='📋 Trip Manager', weight='bold', size='xl', align='center'),
            TextComponent(text='เลือกคำสั่งที่ต้องการใช้งาน', size='sm', color='#999999', align='center')
        ]),
        footer=BoxComponent(layout='horizontal', spacing='sm', contents=[
            ButtonComponent(style='primary', action=MessageAction(label='🚀 สร้างทริป', text='ทริป ')),
            ButtonComponent(style='secondary', action=MessageAction(label='💰 ยอดรวม', text='ยอด')),
            ButtonComponent(style='secondary', action=MessageAction(label='✏️ แก้ไข', text='edit')),
            ButtonComponent(style='secondary', action=MessageAction(label='🎤 Showtime', text='showtime')),
            ButtonComponent(style='secondary', action=MessageAction(label='❓ เมนู', text='เมนู'))
        ])
    ))

def build_showtime_menu_flex(end_date=None):
    info_text = f"สิ้นสุด: {end_date}" if end_date else "ไม่ได้กำหนดวันสิ้นสุด"
    return FlexSendMessage(alt_text="Showtime Menu", contents=BubbleContainer(
        body=BoxComponent(layout='vertical', contents=[
            TextComponent(text='🎤 Showtime Mode', weight='bold', size='xl', color='#FF5551'),
            TextComponent(text=f'สถานะ: เปิดอยู่\n{info_text}', size='sm', wrap=True)
        ]),
        footer=BoxComponent(layout='vertical', spacing='sm', contents=[
            ButtonComponent(style='primary', action=MessageAction(label='💾 บันทึก & ออก', text='save')),
            ButtonComponent(style='secondary', action=MessageAction(label='👁️ ดูตาราง', text='showtime')),
            ButtonComponent(style='secondary', action=MessageAction(label='✏️ แก้ไขข้อความ', text='editshowtime')),
            ButtonComponent(style='secondary', action=MessageAction(label='🛑 จบ Showtime', text='end showtime'))
        ])
    ))

# =================================================================
# Showtime Management
# =================================================================
def load_showtime():
    try:
        res = supabase.table("showtimes").select("*").execute()
        schedule = res.data if res.data else []
        return {"schedule": schedule, "last_updated": datetime.now().isoformat()}
    except Exception as e:
        logger.error(f"Load showtime error: {e}")
        return {"schedule": [], "last_updated": None}

def save_showtime(showtime_data):
    try:
        supabase.table("showtimes").delete().neq("id", 0).execute()
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
    def get_sort_key(item):
        time_str = item.get("time", "00:00").split('-')[0]
        try:
            h, m = map(int, time_str.split(':'))
            return (1, h * 60 + m) if 0 <= h < 9 else (0, h * 60 + m)
        except: return (2, 0)
    return sorted(schedule, key=get_sort_key)

def format_showtime_message():
    showtime = load_showtime()
    if not showtime.get("schedule"): return "ℹ️ ยังไม่มีข้อมูล Showtime"
    sorted_schedule = sort_showtime_by_time(showtime.get("schedule", []))
    msg = "📋 **ตารางการแสดง:**\n\n"
    for item in sorted_schedule:
        msg += f"⏱️ {item.get('time', '-')} | 🎤 {item.get('artist', '-')}\n"
    return msg

# =================================================================
# Currency & Expense Helpers
# =================================================================
CURRENCY_RATES = {"THB": 1, "JPY": 0.23, "USD": 34.5, "KRW": 0.025}

def get_exchange_rate(from_curr, to_curr):
    if from_curr == to_curr: return 1.0
    try:
        url = f"https://api.exchangerate-api.com/v4/latest/{from_curr}"
        resp = requests.get(url, timeout=5)
        return resp.json().get("rates", {}).get(to_curr, CURRENCY_RATES.get(to_curr, 1.0))
    except: return CURRENCY_RATES.get(to_curr, 1.0)

def parse_enhanced_expense(text):
    pattern = r'^(.+?)\s+(.+?)\s+(\d+(?:,\d{3})*(?:\.\d{2})?)\s*([A-Za-z]{3})?\s*(#.*)?$'
    match = re.match(pattern, text.strip(), re.IGNORECASE)
    if match:
        payer = match.group(1).strip()
        item = match.group(2).strip()
        amount = float(match.group(3).replace(',', ''))
        currency = (match.group(4) or "THB").upper()
        tag = match.group(5).strip() if match.group(5) else None
        return payer, item, amount, currency, tag
    return None, None, None, None, None

def extract_amount(text):
    if not text: return None
    lines = text.split('\n')
    amount_labels = ['จำนวน', 'amount']
    for i, line in enumerate(lines):
        line_lower = line.lower().strip()
        if any(label in line_lower for label in amount_labels):
            amounts = re.findall(r'(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)', line)
            for a in amounts:
                try:
                    num = float(a.replace(',', ''))
                    if 1 <= num <= 1000000: return num
                except: continue
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                amounts = re.findall(r'(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)', next_line)
                for a in amounts:
                    try:
                        num = float(a.replace(',', ''))
                        if 1 <= num <= 1000000: return num
                    except: continue
    baht_matches = re.findall(r'(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)\s*บาท', text)
    for a in baht_matches:
        try:
            num = float(a.replace(',', ''))
            if 1 <= num <= 1000000: return num
        except: continue
    return None

def extract_showtime(text):
    if not text: return []
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    showtime_list = []
    time_pattern = r'(\d{1,2}[:.]\d{2})\s*[-–]\s*(\d{1,2}[:.]\d{2})'
    for i, line in enumerate(lines):
        time_match = re.search(time_pattern, line)
        if not time_match: continue
        time_str = f"{time_match.group(1)}-{time_match.group(2)}".replace('.', ':')
        artist = None
        line_without_time = re.sub(time_pattern, '', line).strip()
        line_without_time = re.sub(r'^[⏱️🎤📋✏️🎵]+\s*', '', line_without_time).strip()
        if line_without_time and len(line_without_time) > 1: artist = line_without_time
        if not artist:
            for j in range(i - 1, max(i - 3, -1), -1):
                prev_line = lines[j].strip()
                if re.search(time_pattern, prev_line) or re.match(r'^[\d\s\-:.–]+$', prev_line): continue
                prev_clean = re.sub(r'^[⏱️🎤📋✏️🎵]+\s*', '', prev_line).strip()
                if prev_clean and len(prev_clean) > 1: artist = prev_clean; break
        if not artist:
            for j in range(i + 1, min(i + 3, len(lines))):
                next_line = lines[j].strip()
                if re.search(time_pattern, next_line) or re.match(r'^[\d\s\-:.–]+$', next_line): continue
                next_clean = re.sub(r'^[⏱️🎤📋✏️🎵]+\s*', '', next_line).strip()
                if next_clean and len(next_clean) > 1: artist = next_clean; break
        if not artist: artist = "Unknown"
        showtime_list.append({"time": time_str, "artist": artist})
    return showtime_list

# =================================================================
# Core DB Functions
# =================================================================
def get_active_trip(user_id, group_id=None):
    try:
        if group_id:
            res = supabase.table("trips").select("*").eq("status", "active").eq("line_group_id", group_id).order("created_at", desc=True).limit(1).execute()
            if res.data: return res.data[0]
        res = supabase.table("trips").select("*").eq("status", "active").eq("creator_id", user_id).order("created_at", desc=True).limit(1).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"Get Active Trip Error: {e}")
        return None

def get_display_name(user_id, group_id=None):
    try:
        if group_id: profile = line_bot_api.get_group_member_profile(group_id, user_id)
        else: profile = line_bot_api.get_profile(user_id)
        return profile.display_name
    except: return user_id[:8]

def get_all_expenses(trip_id):
    try:
        res = supabase.table("expenses").select("*").eq("trip_id", trip_id).order("id", desc=False).execute()
        return res.data if res.data else []
    except: return []

def update_expense_amount(expense_id, new_amount):
    try:
        supabase.table("expenses").update({"amount": new_amount}).eq("id", expense_id).execute()
        return True
    except: return False

def get_total_expenses(trip_id):
    try:
        expenses = get_all_expenses(trip_id)
        if not expenses: return 0, {}
        total = sum(e['amount'] for e in expenses)
        user_totals = {}
        for e in expenses:
            uid = e['line_user_id']
            user_totals[uid] = user_totals.get(uid, 0) + e['amount']
        return total, user_totals
    except: return 0, {}

def load_schedules():
    try:
        res = supabase.table("schedules").select("*").order("created_at", desc=True).execute()
        return res.data if res.data else []
    except: return []

def get_active_events():
    schedules = load_schedules()
    return [s for s in schedules if s.get('active', True)]

# =================================================================
# Export Excel Functions
# =================================================================
def export_trip_to_excel(trip_id, trip_title):
    try:
        expenses = get_all_expenses(trip_id)
        if not expenses: return None, "ไม่มีข้อมูลค่าใช้จ่ายในทริปนี้"
        data = []
        for exp in expenses:
            user_name = exp.get('payer_name') or get_display_name(exp['line_user_id'], None)
            created = exp.get('created_at', '')
            date_str, time_str = '', ''
            if created:
                try:
                    dt = datetime.fromisoformat(created.replace('Z', '+00:00')) + timedelta(hours=7)
                    date_str = dt.strftime('%Y-%m-%d')
                    time_str = dt.strftime('%H:%M:%S')
                except: date_str, time_str = created[:10], created[11:19]
            data.append({"ชื่อทริป": trip_title, "วันที่": date_str, "เวลา": time_str, "ชื่อผู้จ่าย": user_name, "รายการ": exp.get('item_name', ''), "จำนวนเงิน": exp.get('amount', 0), "สกุล": exp.get('currency', 'THB'), "หมวดหมู่": exp.get('tag', '')})
        df = pd.DataFrame(data)
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer: df.to_excel(writer, index=False, sheet_name='Expenses')
        output.seek(0)
        return output, None
    except Exception as e: return None, str(e)

def upload_excel_to_supabase(file_buffer, filename):
    try:
        supabase.storage.from_("trip-exports").upload(path=filename, file=file_buffer.getvalue(), file_options={"content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "upsert": "true"})
        return supabase.storage.from_("trip-exports").get_public_url(filename), None
    except Exception as e: return None, str(e)

# =================================================================
# API Endpoints
# =================================================================
@app.route("/api/event-time", methods=["GET"])
def get_event_time():
    url = request.args.get("url", "")
    if not url: return jsonify({"ok": False, "error": "ไม่มี URL"}), 400
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        patterns = [r'(\d{1,2})\s+(มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|กรกฎาคม|สิงหาคม|กันยายน|ตุลาคม|พฤศจิกายน|ธันวาคม)\s+(\d{3,4})', r'(\d{1,2})[-/](\d{1,2})[-/](\d{4})\s+(\d{1,2}):(\d{2})']
        matched = ""
        for p in patterns:
            m = re.search(p, resp.text, re.IGNORECASE)
            if m: matched = m.group(0); break
        return jsonify({"ok": True, "matchedText": matched})
    except Exception as e: return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/line-push", methods=["POST"])
def line_push():
    data = request.json
    target_id, message = data.get("targetId"), data.get("message")
    if not target_id or not message: return jsonify({"ok": False, "error": "ต้องระบุ targetId และ message"}), 400
    try:
        line_bot_api.push_message(target_id, TextSendMessage(text=message))
        return jsonify({"ok": True})
    except Exception as e: return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/config-status", methods=["GET"])
def config_status():
    return jsonify({"ok": True, "lineTokenConfigured": bool(os.getenv('LINE_CHANNEL_ACCESS_TOKEN')), "lineSecretConfigured": bool(os.getenv('LINE_CHANNEL_SECRET'))})

@app.route("/api/schedules", methods=["GET", "POST"])
def handle_schedules():
    if request.method == "GET":
        schedules = load_schedules()
        formatted = [{"id": str(s["id"]), "targetId": s.get("target_id", ""), "buyerName": s.get("buyer_name", ""), "name": s.get("name", ""), "url": s.get("url", ""), "saleTime": s.get("sale_time", ""), "site": s.get("site", ""), "active": s.get("active", True), "createdAt": s.get("created_at", "")} for s in schedules]
        return jsonify({"ok": True, "schedules": formatted})
    elif request.method == "POST":
        try:
            data = request.json
            new_s = supabase.table("schedules").insert({"target_id": data.get("targetId", ""), "buyer_name": data.get("buyerName", ""), "name": data.get("name", ""), "url": data.get("url", ""), "sale_time": data.get("saleTime", ""), "site": data.get("site", ""), "active": True}).execute()
            if new_s.data:
                ns = new_s.data[0]
                return jsonify({"ok": True, "schedule": {"id": str(ns["id"]), "targetId": ns.get("target_id", ""), "buyerName": ns.get("buyer_name", ""), "name": ns.get("name", ""), "url": ns.get("url", ""), "saleTime": ns.get("sale_time", ""), "site": ns.get("site", ""), "active": ns.get("active", True), "createdAt": ns.get("created_at", "")}})
            return jsonify({"ok": False, "error": "Failed to add schedule"}), 500
        except Exception as e: return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/schedules/<schedule_id>", methods=["DELETE"])
def delete_schedule(schedule_id):
    try:
        supabase.table("schedules").delete().eq("id", schedule_id).execute()
        return jsonify({"ok": True})
    except Exception as e: return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/server-time", methods=["GET"])
def get_server_time():
    import time
    return jsonify({"ok": True, "serverTime": int(time.time() * 1000)})

# [NEW] Daily Summary Cron Endpoint
@app.route("/api/daily-summary", methods=["POST"])
def daily_summary_cron():
    auth = request.headers.get("Authorization", "")
    secret = os.getenv("CRON_SECRET", "")
    if secret and auth != f"Bearer {secret}":
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    
    try:
        today_str = (datetime.now() + timedelta(hours=7)).strftime("%Y-%m-%d")
        # หาทริปที่ยัง active อยู่ทั้งหมด
        trips_res = supabase.table("trips").select("*").eq("status", "active").execute()
        trips = trips_res.data if trips_res.data else []
        
        sent_count = 0
        for trip in trips:
            expenses = get_all_expenses(trip['id'])
            today_exp = [e for e in expenses if e.get('created_at', '').startswith(today_str)]
            
            if not today_exp: continue
            
            total_thb = 0
            categories = {}
            for exp in today_exp:
                amt_thb = exp['amount']
                curr = exp.get('currency', 'THB')
                if curr != 'THB': amt_thb *= (get_exchange_rate(curr, 'THB') or 1.0)
                total_thb += amt_thb
                tag = exp.get('tag') or '#ทั่วไป'
                if tag not in categories: categories[tag] = {'total': 0, 'payers': set()}
                categories[tag]['total'] += amt_thb
                payer = exp.get('payer_name') or get_display_name(exp['line_user_id'], trip.get('line_group_id'))
                categories[tag]['payers'].add(payer)
            
            # สร้างข้อความสรุป
            msg = f"📊 **สรุปยอดประจำวัน ({today_str})**\n🚀 ทริป: {trip['title']}\n\n"
            msg += f"💵 **ยอดรวมวันนี้: {total_thb:,.2f} บาท**\n\n"
            for tag, data in sorted(categories.items()):
                payers_str = ", ".join(sorted(data['payers']))
                msg += f"{tag} {data['total']:,.0f} บ. ({payers_str})\n"
            
            # ส่งไปยังกลุ่มหรือผู้สร้างทริป
            target = trip.get('line_group_id') or trip.get('creator_id')
            if target:
                line_bot_api.push_message(target, TextSendMessage(text=msg))
                sent_count += 1
            
            # บันทึก log ลง DB
            details = {tag: {"total": d['total'], "payers": list(d['payers'])} for tag, d in categories.items()}
            supabase.table("daily_summaries").upsert({
                "trip_id": trip['id'], "summary_date": today_str,
                "total_thb": total_thb, "details": details
            }, on_conflict="trip_id,summary_date").execute()
        
        return jsonify({"ok": True, "sent": sent_count, "date": today_str})
    except Exception as e:
        logger.error(f"Daily summary cron error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

# =================================================================
# LINE Bot Handlers
# =================================================================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try: handler.handle(body, signature)
    except InvalidSignatureError: abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    text = event.message.text.strip()
    text_lower = text.lower()
    user_id = event.source.user_id
    group_id = getattr(event.source, 'group_id', None)
    reply_token = event.reply_token
    state = get_state(user_id)

    # === Showtime Commands ===
    if text_lower == "showtime":
        if state and state.get("action") == "showtime_mode":
            line_bot_api.reply_message(reply_token, [TextSendMessage(text=format_showtime_message()), build_showtime_menu_flex(state.get("end_date"))])
            return
        set_state(user_id, {"action": "wait_showtime_date"})
        line_bot_api.reply_message(reply_token, TextSendMessage(text="📅 กรุณาระบุวันที่สิ้นสุด Showtime (YYYY-MM-DD)\nเช่น 2026-05-30\n(พิมพ์ 'ข้าม' หากไม่ต้องการกำหนด)"))
        return

    if state and state.get("action") == "wait_showtime_date":
        end_date = None
        if text.strip().lower() != "ข้าม":
            try: end_date = datetime.strptime(text.strip(), "%Y-%m-%d").strftime("%Y-%m-%d")
            except ValueError:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ รูปแบบวันที่ไม่ถูกต้อง กรุณาใช้ YYYY-MM-DD หรือพิมพ์ 'ข้าม'"))
                return
        set_state(user_id, {"action": "showtime_mode", "end_date": end_date, "edit_mode": False})
        msg = format_showtime_message() + "\n\n📸 ส่งรูป Showtime ใหม่เพื่ออัปเดต"
        line_bot_api.reply_message(reply_token, [TextSendMessage(text=msg), build_showtime_menu_flex(end_date)])
        return

    if text_lower in ["end showtime", "stop showtime"]:
        if state and state.get("action") == "showtime_mode":
            clear_state(user_id)
            line_bot_api.reply_message(reply_token, TextSendMessage(text="✅ ออกจากโหมด Showtime เรียบร้อย\n📸 กลับมารับสลิปปกติแล้วครับ"))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="ℹ️ ไม่ได้อยู่ในโหมด Showtime ครับ"))
        return

    if text_lower == "save":
        if state and state.get("action") == "showtime_mode":
            if state.get("showtime_temp"):
                existing = load_showtime()
                existing["schedule"] = sort_showtime_by_time(state["showtime_temp"])
                existing["last_updated"] = datetime.now().isoformat()
                save_showtime(existing)
            clear_state(user_id)
            line_bot_api.reply_message(reply_token, TextSendMessage(text="✅ บันทึก Showtime เสร็จ!\n\n📸 ตอนนี้สลิปทำงานปกติแล้ว"))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่ได้อยู่ในโหมด Showtime"))
        return

    if text_lower in ["editshowtime", "update showtime"]:
        existing = load_showtime()
        if not existing.get("schedule"):
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ยังไม่มี Showtime ให้แก้ไข"))
            return
        msg = "✏️ แก้ไข Showtime (รองรับหลายบรรทัด)\n" + format_showtime_message()
        msg += "\n\n📝 พิมพ์เช่น:\n13:00-13:50 ROMANCE\n14:00-14:50 SWEET MULLET"
        set_state(user_id, {"action": "showtime_mode", "edit_mode": True, "end_date": state.get("end_date") if state else None})
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return

    if state and state.get("action") == "showtime_mode" and state.get("edit_mode"):
        lines = text.splitlines()
        updated = False
        existing = load_showtime()
        schedule = existing.get("schedule", [])
        time_pattern_input = r'^(\d{1,2}[:.]\d{2})\s*[-–]\s*(\d{1,2}[:.]\d{2})\s+(.+)$'
        for line in lines:
            line = line.strip()
            if not line: continue
            match = re.match(time_pattern_input, line)
            if match:
                time_input = f"{match.group(1)}-{match.group(2)}".replace('.', ':')
                artist_input = match.group(3).strip()
                found = False
                for item in schedule:
                    if item["time"] == time_input: item["artist"] = artist_input; found = True; break
                if not found: schedule.append({"time": time_input, "artist": artist_input})
                updated = True
        if updated:
            existing["schedule"] = sort_showtime_by_time(schedule)
            existing["last_updated"] = datetime.now().isoformat()
            save_showtime(existing)
            line_bot_api.reply_message(reply_token, TextSendMessage(text="✅ อัปเดต Showtime เสร็จ!\n\n" + format_showtime_message() + "\n\n📝 พิมพ์เพิ่มเติม หรือ 'save' เพื่อสิ้นสุด"))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่พบรูปแบบที่ถูกต้อง\n👉 ตัวอย่าง: 13:00-13:50 ROMANCE"))
        return

    # === Menu ===
    if text in ["เมนู", "menu", "help"]:
        if state and state.get("action") == "showtime_mode":
            line_bot_api.reply_message(reply_token, build_showtime_menu_flex(state.get("end_date")))
        else:
            line_bot_api.reply_message(reply_token, build_main_menu_flex())
        return

    # === Showtime Guard ===
    if state and state.get("action") == "showtime_mode":
        allowed = ["save", "showtime", "editshowtime", "update showtime", "เมนู", "menu", "ยกเลิก", "cancel", "end showtime", "stop showtime"]
        if text_lower not in allowed and text not in allowed:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⏸️ ตอนนี้อยู่ในโหมด Showtime\nพิมพ์ 'menu' เพื่อดูคำสั่ง"))
            return

    # === Cancel ===
    if text in ["ยกเลิก", "cancel"]:
        if state:
            action = state.get("action")
            clear_state(user_id)
            if action == "showtime_mode":
                line_bot_api.reply_message(reply_token, TextSendMessage(text="✅ ออกจากโหมด Showtime เรียบร้อย"))
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="✅ ยกเลิกโหมดแก้ไขเรียบร้อย"))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="ℹ️ ไม่มีโหมดแก้ไขที่กำลังทำงานอยู่"))
        return

    # === Edit Commands ===
    if text_lower.startswith("edit"):
        trip = get_active_trip(user_id, group_id)
        if not trip:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่มีทริปที่กำลังทำงานอยู่"))
            return
        edit_parts = text_lower.split()
        if len(edit_parts) == 3 and edit_parts[0] == "edit":
            try:
                inline_id, inline_amount = int(edit_parts[1]), float(edit_parts[2].replace(',', ''))
                if inline_amount > 0:
                    expenses = get_all_expenses(trip['id'])
                    selected = next((e for e in expenses if e['id'] == inline_id), None)
                    if selected and update_expense_amount(inline_id, inline_amount):
                        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ แก้ไข ID {inline_id:04d} จาก {selected['amount']:,.2f} เป็น {inline_amount:,.2f} บาท"))
                    else:
                        line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ แก้ไขไม่สำเร็จ หรือไม่พบ ID"))
                    return
            except: pass
        
        expenses = get_all_expenses(trip['id'])
        if not expenses:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่มีรายการค่าใช้จ่ายให้แก้ไข"))
            return
        if state: clear_state(user_id)
        msg = "✏️ เลือกรายการที่ต้องการแก้ไข (พิมพ์ ID 4 หลัก):\n=======================\n"
        for exp in expenses:
            short_name = exp['item_name'][:35]
            msg += f"ID {exp['id']:04d}. {short_name}\n   💰 {exp['amount']:,.2f} บาท\n"
        msg += "\n=======================\n👉 พิมพ์ 'edit 0042 500' เพื่อเปลี่ยน ID 42 เป็น 500 บาท"
        set_state(user_id, {"action": "edit_selection", "expenses": expenses})
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return

    if state and state.get("action") == "edit_selection":
        expenses = state["expenses"]
        parts = text_lower.split()
        try:
            if len(parts) == 1:
                expense_id = int(parts[0])
                selected = next((e for e in expenses if e['id'] == expense_id), None)
                if selected:
                    set_state(user_id, {"action": "edit_amount", "expense_id": selected['id'], "expense_item": selected['item_name'], "old_amount": selected['amount']})
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✏️ แก้ไข ID {selected['id']:04d}: {selected['item_name'][:50]}\n💰 ยอดเดิม: {selected['amount']:,.2f} บาท\n\n👉 พิมพ์จำนวนเงินใหม่"))
                else:
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"⚠️ ไม่พบรายการ ID {expense_id}"))
                    clear_state(user_id)
            elif len(parts) >= 2:
                expense_id, new_amount = int(parts[0]), float(parts[1].replace(',', ''))
                selected = next((e for e in expenses if e['id'] == expense_id), None)
                if selected and new_amount > 0 and update_expense_amount(selected['id'], new_amount):
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ แก้ไข ID {selected['id']:04d} จาก {selected['amount']:,.2f} เป็น {new_amount:,.2f} บาท"))
                else:
                    line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่พบ ID หรือจำนวนเงินไม่ถูกต้อง"))
                clear_state(user_id)
        except:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาระบุ ID และจำนวนเงินให้ถูกต้อง"))
            clear_state(user_id)
        return

    if state and state.get("action") == "edit_amount":
        try:
            new_amount = float(text_lower.replace(',', ''))
            if new_amount <= 0: raise ValueError
            eid = state["expense_id"]
            old = state["old_amount"]
            item = state["expense_item"]
            if update_expense_amount(eid, new_amount):
                line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ แก้ไข ID {eid:04d} ({item[:30]}) จาก {old:,.2f} เป็น {new_amount:,.2f} บาท"))
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ แก้ไขไม่สำเร็จ"))
        except:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาพิมพ์จำนวนเงินเป็นตัวเลข"))
        clear_state(user_id)
        return

    # === ID Command ===
    if text == "id":
        msg = f"🔑 User ID: {user_id}"
        if group_id: msg += f"\n👥 Group ID: {group_id}"
        else: msg += "\nℹ️ แชทนี้เป็น DM"
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return

    # === Create Trip ===
    if text.startswith("ทริป ") or text_lower.startswith("trip "):
        trip_name = text[5:].strip() if text.startswith("ทริป ") else text[5:].strip()
        if not trip_name:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาระบุชื่อทริป"))
            return
        try:
            supabase.table("trips").update({"status": "closed"}).eq("creator_id", user_id).execute()
            supabase.table("trips").insert({"title": trip_name, "status": "active", "line_group_id": group_id, "creator_id": user_id}).execute()
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"🚀 เริ่มทริปใหม่: {trip_name} เรียบร้อย!"))
        except Exception as e:
            logger.error(f"Create trip error: {e}")
            line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ สร้างทริปไม่สำเร็จ"))
        return

    # === Enhanced 'ยอด' Command ===
    if text == "ยอด" or text_lower == "sum":
        trip = get_active_trip(user_id, group_id)
        if not trip:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่มีทริปที่กำลังทำงานอยู่"))
            return
        expenses = get_all_expenses(trip['id'])
        if not expenses:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="💵 ยังไม่มีรายการค่าใช้จ่าย"))
            return
        total_thb = 0
        categories = {}
        for exp in expenses:
            amt_thb = exp['amount']
            curr = exp.get('currency', 'THB')
            if curr != 'THB': amt_thb *= (get_exchange_rate(curr, 'THB') or 1.0)
            total_thb += amt_thb
            tag = exp.get('tag') or '#ทั่วไป'
            if tag not in categories: categories[tag] = {'total': 0, 'payers': set()}
            categories[tag]['total'] += amt_thb
            payer = exp.get('payer_name') or get_display_name(exp['line_user_id'], group_id)
            categories[tag]['payers'].add(payer)
        msg = f"💵 **ยอดรวมทั้งหมด: {total_thb:,.2f} บาท**\n\n"
        for tag, data in sorted(categories.items()):
            payers_str = ", ".join(sorted(data['payers']))
            msg += f"{tag} {data['total']:,.0f} บ. ({payers_str})\n"
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return

    # === Daily Report Multi-Currency ===
    if text_lower.startswith("ยอดวันนี้"):
        parts = text.split()
        target_curr = parts[1].upper() if len(parts) > 1 else "THB"
        today_str = (datetime.now() + timedelta(hours=7)).strftime("%Y-%m-%d")
        trip = get_active_trip(user_id, group_id)
        if not trip:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่มีทริปที่กำลังทำงานอยู่"))
            return
        expenses = get_all_expenses(trip['id'])
        today_exp = [e for e in expenses if e.get('created_at', '').startswith(today_str)]
        if not today_exp:
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"ℹ️ วันนี้ ({today_str}) ยังไม่มีรายจ่าย"))
            return
        total_target = 0
        rate_to_target = get_exchange_rate("THB", target_curr) if target_curr != "THB" else 1.0
        msg = f"📊 **ยอดวันนี้ ({today_str})**\n"
        if target_curr != "THB": msg += f"💱 แปลงเป็น {target_curr} (Rate: {rate_to_target:.4f})\n\n"
        for exp in today_exp:
            amt_thb = exp['amount']
            curr = exp.get('currency', 'THB')
            if curr != 'THB': amt_thb *= (get_exchange_rate(curr, 'THB') or 1.0)
            converted = amt_thb * rate_to_target
            total_target += converted
            name = exp.get('payer_name') or get_display_name(exp['line_user_id'], group_id)
            msg += f"• {exp['item_name']}: {converted:,.2f} {target_curr} ({name})\n"
        msg += f"\n💰 **รวมวันนี้: {total_target:,.2f} {target_curr}**"
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return

    # === End Trip ===
    if text.startswith("จบทริป") or text_lower.startswith("end trip"):
        trip = get_active_trip(user_id, group_id)
        if not trip:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่มีทริปที่กำลังทำงานอยู่"))
            return
        currency_code = "THB"
        if text.startswith("จบทริป"):
            parts = text.split()
            if len(parts) >= 2: currency_code = parts[1].upper()
        elif text_lower.startswith("end trip"):
            parts = text_lower.split()
            if len(parts) >= 3: currency_code = parts[2].upper()
        set_state(user_id, {"action": "end_trip", "trip_id": trip['id'], "trip_title": trip['title'], "currency_code": currency_code})
        if currency_code != "THB":
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"🏁 ปิดทริป: {trip['title']}\n💱 แปลงเป็น: {currency_code}\n\n👥 ระบุจำนวนคนที่จะหาร:"))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"🏁 ปิดทริป: {trip['title']}\n\n👥 ระบุจำนวนคนที่จะหาร:"))
        return

    if state and state.get("action") == "end_trip":
        try:
            num_people = int(text)
            if num_people <= 0: raise ValueError
        except:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาระบุจำนวนคนเป็นตัวเลขที่มากกว่า 0"))
            return
        trip_id = state["trip_id"]
        trip_title = state["trip_title"]
        currency_code = state.get("currency_code", "THB")
        total, user_totals = get_total_expenses(trip_id)
        if total == 0:
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"🚀 ทริป: {trip_title}\n⚠️ ไม่มีรายการค่าใช้จ่าย"))
            clear_state(user_id)
            return
        avg = total / num_people
        exchange_rate = 1
        if currency_code != "THB":
            rate = get_exchange_rate("THB", currency_code)
            if rate: exchange_rate = rate
            else: currency_code = "THB"
        msg = f"🚀 ทริป: {trip_title}\n👥 จำนวนคน: {num_people}\n\n"
        if currency_code != "THB" and exchange_rate != 1:
            msg += f"💱 อัตราแลกเปลี่ยน: 1 THB = {exchange_rate:.4f} {currency_code}\n\n"
            msg += f"📉 ยอดหารเฉลี่ย:\n   • {avg:,.2f} บาท/คน\n   • ≈ {avg * exchange_rate:,.2f} {currency_code}/คน\n\n"
        else:
            msg += f"📉 ยอดหารเฉลี่ย: {avg:,.2f} บาท/คน\n\n"
        msg += "💵 ยอดสรุปสุทธิ:\n"
        for uid, amt in user_totals.items():
            name = get_display_name(uid, group_id)
            diff = amt - avg
            if currency_code != "THB" and exchange_rate != 1:
                if diff > 0: msg += f"• {name}: รับคืน {diff:,.2f} บาท (≈ {diff * exchange_rate:,.2f} {currency_code})\n"
                elif diff < 0: msg += f"• {name}: จ่ายเพิ่ม {abs(diff):,.2f} บาท (≈ {abs(diff) * exchange_rate:,.2f} {currency_code})\n"
                else: msg += f"• {name}: เรียบร้อยแล้ว\n"
            else:
                if diff > 0: msg += f"• {name}: รับคืน {diff:,.2f} บาท\n"
                elif diff < 0: msg += f"• {name}: จ่ายเพิ่ม {abs(diff):,.2f} บาท\n"
                else: msg += f"• {name}: เรียบร้อยแล้ว\n"
        try: supabase.table("trips").update({"status": "closed", "currency_code": currency_code}).eq("id", trip_id).execute()
        except Exception as e: logger.error(f"Close trip error: {e}")
        clear_state(user_id)
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return

    # === Excel Export ===
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
        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ สร้าง Excel สำเร็จ!\n📊 ทริป: {trip['title']}\n🔗 {public_url}"))
        return

    # === History ===
    if text in ["ประวัติ", "history"]:
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
                msg += f"{i}. {status_icon} {trip['title']}\n   📅 {start_date} → {end_date}\n   👉 พิมพ์: excel {i}\n\n"
            set_state(user_id, {"action": "export_history", "trips": all_trips})
            line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        except Exception as e:
            logger.error(f"History error: {e}")
            line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ ไม่สามารถดึงข้อมูลได้"))
        return

    if state and state.get("action") == "export_history":
        parts = text_lower.split()
        if len(parts) == 2 and parts[0] == "excel":
            try:
                choice = int(parts[1]) - 1
                trips = state["trips"]
                if 0 <= choice < len(trips):
                    selected = trips[choice]
                    excel_buffer, error = export_trip_to_excel(selected['id'], selected['title'])
                    if error:
                        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"❌ {error}"))
                        clear_state(user_id)
                        return
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    filename = f"{selected['title']}_{timestamp}.xlsx"
                    public_url, upload_error = upload_excel_to_supabase(excel_buffer, filename)
                    if upload_error:
                        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"❌ {upload_error}"))
                        clear_state(user_id)
                        return
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ Excel สำเร็จ!\n📊 {selected['title']}\n🔗 {public_url}"))
                else:
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"⚠️ หมายเลขไม่ถูกต้อง (มี 1-{len(trips)})"))
                    return
            except ValueError:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาพิมพ์ตัวเลข เช่น excel 1"))
                return
            except Exception as e:
                logger.error(f"Export history error: {e}")
                line_bot_api.reply_message(reply_token, TextSendMessage(text=f"❌ เกิดข้อผิดพลาด: {str(e)}"))
            clear_state(user_id)
            return
        else:
            clear_state(user_id)

    # === Enhanced Expense Recording ===
    if not text.startswith(("ทริป", "ยอด", "จบทริป", "เมนู", "ยกเลิก", "ประวัติ", "excel")) and \
       not text_lower.startswith(("trip", "sum", "end", "id", "event", "stop", "edit", "menu", "cancel", "history", "excel")):
        trip = get_active_trip(user_id, group_id)
        if trip and not (state and state.get("action") == "showtime_mode"):
            payer, item, amount, currency, tag = parse_enhanced_expense(text)
            if amount and amount > 0:
                try:
                    supabase.table("expenses").insert({
                        "trip_id": trip['id'], "line_user_id": user_id,
                        "payer_name": payer if payer != user_id else None,
                        "amount": amount, "item_name": item or "ค่าใช้จ่าย",
                        "currency": currency, "tag": tag, "slip_url": None
                    }).execute()
                    curr_txt = f" {currency}" if currency != "THB" else ""
                    tag_txt = f" ({tag})" if tag else ""
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ บันทึก {amount:,.2f}{curr_txt} จากคุณ {payer}{tag_txt} สำเร็จ!"))
                except Exception as e:
                    logger.error(f"Save expense error: {e}")
            return

    # === Event Commands ===
    if text_lower == "event":
        events = get_active_events()
        base_url = "https://line-chat-bot-trip-manager.onrender.com"
        if not events:
            msg = f"🔍 ตรวจสอบรายชื่อคิว Event ปัจจุบัน...\n=======================\nℹ️ ไม่มีคิว Event ที่เปิดอยู่\n-----------------------\n\n💻 ลิงก์ควบคุม:\n{base_url}"
        else:
            msg = "🔍 ตรวจสอบรายชื่อคิว Event ปัจจุบัน...\n=======================\n"
            for i, e in enumerate(events, 1):
                msg += f"{i}. งาน: {e.get('name', '-')}\n⏰ เวลาขาย: {e.get('saleTime', '-')}\n🔗 ลิงก์: {e.get('url', '-')}\n-----------------------\n"
            msg += f"\n💻 ลิงก์ควบคุม:\n{base_url}"
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return

    if text_lower == "stop event":
        events = get_active_events()
        if not events:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่มี Event ที่กำลังทำงานอยู่"))
            return
        set_state(user_id, {"action": "stop_event", "events": events})
        msg = "🚫 เลือกหมายเลข Event ที่ต้องการหยุด:\n=======================\n"
        for i, e in enumerate(events, 1):
            msg += f"{i}. งาน: {e.get('name', '-')}\n🛑 (ID: {e.get('id', '-')})\n-----------------------\n"
        msg += "👉 พิมพ์เฉพาะตัวเลขลำดับ"
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return

    if state and state.get("action") == "stop_event":
        try:
            choice = int(text_lower) - 1
            events = state["events"]
            if 0 <= choice < len(events):
                selected = events[choice]
                supabase.table("schedules").update({"active": False}).eq("id", selected.get('id')).execute()
                line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ สั่งปิดงาน: {selected.get('name', '-')} เรียบร้อย"))
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ หมายเลขไม่ถูกต้อง"))
        except ValueError:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาพิมพ์หมายเลขเท่านั้น"))
        except Exception as e:
            logger.error(f"Stop event error: {e}")
            line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ เกิดข้อผิดพลาด"))
        clear_state(user_id)
        return

# === Slip Payer Prompt ===
@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    user_id = event.source.user_id
    group_id = getattr(event.source, 'group_id', None)
    reply_token = event.reply_token
    state = get_state(user_id)

    # Showtime OCR
    if state and state.get("action") == "showtime_mode":
        try:
            message_content = line_bot_api.get_message_content(event.message.id)
            image_bytes = b''.join(message_content.iter_content())
            response = vision_client.text_detection(image=vision.Image(content=image_bytes))
            text_detected = response.text_annotations[0].description if response.text_annotations else ""
            showtime_list = extract_showtime(text_detected)
            if showtime_list:
                if state.get("edit_mode"):
                    existing = load_showtime()
                    schedule = existing.get("schedule", [])
                    for new_item in showtime_list:
                        found = False
                        for i, old_item in enumerate(schedule):
                            if old_item["time"] == new_item["time"]: schedule[i] = new_item; found = True; break
                        if not found: schedule.append(new_item)
                    existing["schedule"] = sort_showtime_by_time(schedule)
                    existing["last_updated"] = datetime.now().isoformat()
                    save_showtime(existing)
                    msg = "✅ อัปเดต Showtime เสร็จ!\n\n" + format_showtime_message() + "\n\n📝 พิมพ์เพิ่มเติม หรือ 'save'"
                else:
                    state["showtime_temp"] = showtime_list
                    msg = "✅ อ่านข้อมูล Showtime สำเร็จ\n\n📋 **ตารางการแสดง:**\n\n"
                    for item in showtime_list: msg += f"⏱️ {item['time']} | 🎤 {item['artist']}\n"
                    msg += "\n👉 พิมพ์ 'save' เพื่อบันทึก"
                line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่พบข้อมูล Showtime ในรูป"))
        except Exception as e:
            logger.error(f"Showtime OCR error: {e}")
            line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ ไม่สามารถอ่านรูปได้"))
        return

    # Normal Slip → Ask Payer
    trip = get_active_trip(user_id, group_id)
    if not trip:
        line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่มีทริปที่กำลังทำงานอยู่"))
        return
    set_state(user_id, {"action": "wait_slip_payer", "message_id": event.message.id, "trip_id": trip['id'], "group_id": group_id})
    line_bot_api.reply_message(reply_token, TextSendMessage(text="🧾 พบสลิป/บิล\n👤 กรุณาพิมพ์ชื่อคนที่ต้องรับผิดชอบยอดนี้\n(หรือพิมพ์ 'ฉัน' เพื่อใช้ชื่อคุณ)"))

# Handle Slip Payer Name
@handler.add(MessageEvent, message=TextMessage)
def handle_slip_payer_followup(event):
    state = get_state(event.source.user_id)
    if state and state.get("action") == "wait_slip_payer":
        payer_name = event.message.text.strip()
        if payer_name.lower() == "ฉัน":
            payer_name = get_display_name(event.source.user_id, state.get('group_id'))
        threading.Thread(target=process_slip_with_payer, args=(
            state['message_id'], state['trip_id'], event.source.user_id,
            state.get('group_id'), event.reply_token, payer_name
        )).start()
        clear_state(event.source.user_id)

def process_slip_with_payer(message_id, trip_id, user_id, group_id, reply_token, payer_name):
    try:
        message_content = line_bot_api.get_message_content(message_id)
        image_bytes = b''.join(message_content.iter_content())
        response = vision_client.text_detection(image=vision.Image(content=image_bytes))
        text_detected = response.text_annotations[0].description if response.text_annotations else ""
        amount = extract_amount(text_detected)
        if amount:
            timestamp = datetime.now().strftime('%d/%m/%y %H:%M:%S')
            result = supabase.table("expenses").insert({
                "trip_id": trip_id, "line_user_id": user_id, "payer_name": payer_name,
                "amount": amount, "item_name": f"บิล {timestamp}", "currency": "THB", "slip_url": f"slip_{message_id}"
            }).execute()
            new_id = result.data[0]['id'] if result.data else None
            success_msg = f"✅ บันทึก {amount:,.2f} บาท จากคุณ {payer_name} สำเร็จ!"
            if new_id: success_msg += f"\n✏️ แก้ไข: edit {new_id:04d} {amount}"
            line_bot_api.reply_message(reply_token, TextSendMessage(text=success_msg))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่พบจำนวนเงินในรูป\n📌 ลองบันทึกด้วยข้อความ เช่น 'บอล ค่าเหล้า 500'"))
    except Exception as e:
        logger.error(f"Process slip with payer error: {e}")
        line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ ไม่สามารถอ่านรูปได้"))

# =================================================================
# Dashboard Routes
# =================================================================
@app.route("/")
def render_dashboard():
    return render_template("index.html")

@app.route("/static/<path:filename>")
def serve_static(filename):
    return send_from_directory("static", filename)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5177))
    app.run(host="0.0.0.0", port=port, debug=True)