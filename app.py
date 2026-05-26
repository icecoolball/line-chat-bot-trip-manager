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

# =================================================================
# App Initialization
# =================================================================
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- การตั้งค่าเริ่มต้น ---
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))

creds_json = os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON')
if creds_json:
    creds_dict = json.loads(creds_json)
    creds = service_account.Credentials.from_service_account_info(creds_dict)
    vision_client = vision.ImageAnnotatorClient(credentials=creds)
else:
    logger.error("❌ GOOGLE_APPLICATION_CREDENTIALS_JSON missing")
    vision_client = None

supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_ANON_KEY")
if supabase_url and supabase_key:
    supabase: Client = create_client(supabase_url, supabase_key)
else:
    logger.error("❌ Supabase environment variables missing")
    supabase = None

user_state = {}
STATE_TIMEOUT_SECONDS = 600

# =================================================================
# State Management
# =================================================================
def set_state(user_id, data):
    data["_ts"] = datetime.now().timestamp()
    user_state[user_id] = data

def get_state(user_id):
    s = user_state.get(user_id)
    if not s:
        return None
    if s.get("action") == "showtime_mode":
        end_date_str = s.get("end_date")
        if end_date_str:
            try:
                end_dt = datetime.strptime(end_date_str, "%Y-%m-%d")
                if datetime.now() > end_dt + timedelta(days=1):
                    clear_state(user_id)
                    return None
            except:
                pass
        return s
    if datetime.now().timestamp() - s.get("_ts", 0) > STATE_TIMEOUT_SECONDS:
        clear_state(user_id)
        return None
    return s

def clear_state(user_id):
    user_state.pop(user_id, None)

# =================================================================
# Flex Message Builders
# =================================================================
def build_main_menu_flex():
    return FlexSendMessage(
        alt_text="เมนูคำสั่ง",
        contents=BubbleContainer(
            size='mega',
            body=BoxComponent(
                layout='vertical',
                spacing='md',
                contents=[
                    TextComponent(text='📋 Trip Manager', weight='bold', size='xl', align='center', color='#1DB446'),
                    TextComponent(text='เลือกคำสั่งที่ต้องการใช้งาน', size='sm', color='#999999', align='center', wrap=True)
                ]
            ),
            footer=BoxComponent(
                layout='vertical',
                spacing='sm',
                flex=0,
                contents=[
                    ButtonComponent(style='primary', color='#1DB446', height='md', action=MessageAction(label='🚀 สร้างทริป', text='ทริป ')),
                    ButtonComponent(style='secondary', height='md', action=MessageAction(label='💰 ยอดรวม', text='ยอด')),
                    ButtonComponent(style='secondary', height='md', action=MessageAction(label='✏️ แก้ไข', text='edit')),
                    ButtonComponent(style='secondary', height='md', action=MessageAction(label='🎤 Showtime', text='showtime')),
                    ButtonComponent(style='secondary', height='md', action=MessageAction(label='❓ เมนู', text='เมนู'))
                ]
            )
        )
    )

def build_showtime_menu_flex(end_date=None):
    info_text = f"สิ้นสุด: {end_date}" if end_date else "ไม่ได้กำหนดวันสิ้นสุด"
    return FlexSendMessage(
        alt_text="Showtime Menu",
        contents=BubbleContainer(
            size='mega',
            body=BoxComponent(
                layout='vertical',
                spacing='md',
                contents=[
                    TextComponent(text='🎤 Showtime Mode', weight='bold', size='xl', color='#FF5551', align='center'),
                    TextComponent(text=f'สถานะ: เปิดอยู่\n{info_text}', size='sm', wrap=True, align='center', color='#666666', margin='md')
                ]
            ),
            footer=BoxComponent(
                layout='vertical',
                spacing='sm',
                flex=0,
                contents=[
                    ButtonComponent(style='primary', color='#1DB446', height='md', action=MessageAction(label='💾 บันทึก & ออก', text='save')),
                    ButtonComponent(style='secondary', height='md', action=MessageAction(label='👁️ ดูตาราง', text='showtime')),
                    ButtonComponent(style='secondary', height='md', action=MessageAction(label='✏️ แก้ไขข้อความ', text='editshowtime')),
                    ButtonComponent(style='danger', height='md', action=MessageAction(label='🛑 จบ Showtime', text='end showtime'))
                ]
            )
        )
    )

def build_report_flex(title, subtitle, lines, alt_text="รายงาน"):
    safe_lines = lines[:12]
    if len(lines) > 12:
        safe_lines.append(f"...อีก {len(lines)-12} รายการ")
    body_contents = [
        TextComponent(text=title, weight='bold', size='xl', wrap=True),
    ]
    if subtitle:
        body_contents.append(TextComponent(text=subtitle, size='sm', color='#888888', wrap=True))
    for ln in safe_lines:
        body_contents.append(TextComponent(text=ln, size='sm', wrap=True))
    return FlexSendMessage(
        alt_text=alt_text,
        contents=BubbleContainer(
            body=BoxComponent(layout='vertical', spacing='sm', contents=body_contents)
        )
    )

# =================================================================
# Showtime Management
# =================================================================
def load_showtime():
    if not supabase:
        return {"schedule": [], "last_updated": None}
    try:
        res = supabase.table("showtimes").select("*").execute()
        schedule = res.data if res.data else []
        return {"schedule": schedule, "last_updated": datetime.now().isoformat()}
    except Exception as e:
        logger.error(f"Load showtime error: {e}")
        return {"schedule": [], "last_updated": None}

def save_showtime(showtime_data):
    if not supabase:
        return False
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
        except:
            return (2, 0)
    return sorted(schedule, key=get_sort_key)

def format_showtime_message():
    showtime = load_showtime()
    if not showtime.get("schedule"):
        return "ℹ️ ยังไม่มีข้อมูล Showtime"
    sorted_schedule = sort_showtime_by_time(showtime.get("schedule", []))
    msg = "📋 ตารางการแสดง:\n\n"
    for item in sorted_schedule:
        time_display = item.get('time', '-').replace('.', ':')
        artist = item.get('artist', '-')
        msg += f"⏱️ {time_display} | 🎤 {artist}\n"
    return msg

# =================================================================
# Currency & Expense Helpers
# =================================================================
CURRENCY_RATES = {"THB": 1, "JPY": 0.23, "USD": 34.5, "KRW": 0.025}
_RATE_CACHE = {}
_RATE_CACHE_TTL_SECONDS = 1800

def get_exchange_rate(from_curr, to_curr):
    if from_curr == to_curr:
        return 1.0
    from_curr = (from_curr or "").upper()
    to_curr = (to_curr or "").upper()
    key = (from_curr, to_curr)
    now_ts = datetime.now().timestamp()
    cached = _RATE_CACHE.get(key)
    if cached and (now_ts - cached.get("ts", 0) <= _RATE_CACHE_TTL_SECONDS):
        return cached.get("rate", 1.0)
    try:
        url = f"https://api.exchangerate-api.com/v4/latest/{from_curr}"
        resp = requests.get(url, timeout=5)
        rate = resp.json().get("rates", {}).get(to_curr, None)
        if rate:
            _RATE_CACHE[key] = {"rate": float(rate), "ts": now_ts}
            return float(rate)
        fallback = CURRENCY_RATES.get(to_curr, 1.0)
        _RATE_CACHE[key] = {"rate": float(fallback), "ts": now_ts}
        return float(fallback)
    except:
        if cached and cached.get("rate"):
            return cached["rate"]
        return CURRENCY_RATES.get(to_curr, 1.0)

_CURRENCY_ALIASES = {
    "THB": "THB", "JPY": "JPY", "USD": "USD", "KRW": "KRW",
    "บาท": "THB", "baht": "THB",
    "เยน": "JPY", "yen": "JPY",
    "วอน": "KRW", "won": "KRW",
    "ดอลลาร์": "USD", "ดอลลาร์สหรัฐ": "USD", "usd": "USD",
}

def _normalize_currency(token):
    if not token:
        return None
    t = token.strip()
    if not t:
        return None
    return _CURRENCY_ALIASES.get(t.upper(), _CURRENCY_ALIASES.get(t.lower(), t.upper() if re.fullmatch(r"[A-Za-z]{3}", t) else None))

def _parse_amount_token(token):
    if not token:
        return None
    t = token.strip().replace(",", "")
    if not re.fullmatch(r"\d+(?:\.\d{0,2})?", t):
        return None
    try:
        v = float(t)
        return v if v > 0 else None
    except:
        return None

def parse_enhanced_expense(text, default_payer_name=None):
    """ปรับปรุงให้รองรับการไม่ใส่ Tag และ Participants (ให้บอทถามต่อได้)"""
    raw = (text or "").strip()
    if not raw:
        return None, None, None, None, None, None
    parts = [p for p in raw.split() if p.strip()]
    # ลดจำนวนขั้นต่ำเหลือ 2 (เพื่อให้รองรับรูปแบบ "Ball 500" ได้)
    if len(parts) < 2:
        return None, None, None, None, None, None

    amt_idx = None
    amt_val = None
    for i, p in enumerate(parts):
        # แก้ regex ให้ถูกต้อง (เติม \ ก่อนจุดทศนิยม)
        if re.fullmatch(r"\d+(?:\.\d{0,2})?", p.replace(",", "")):
            amt_idx, amt_val = i, float(p.replace(",", ""))
            break
        
    if amt_idx is None:
        return None, None, None, None, None, None

    currency = "THB"
    after_amt_idx = amt_idx + 1
    if after_amt_idx < len(parts):
        maybe_curr = _normalize_currency(parts[after_amt_idx])
        if maybe_curr:
            currency = maybe_curr
            after_amt_idx += 1

    # อนุญาตให้ไม่มี Tag ได้ (ไม่ return None ทิ้ง)
    tag = None
    if after_amt_idx < len(parts) and parts[after_amt_idx].startswith("#"):
        tag = parts[after_amt_idx].strip()
        after_amt_idx += 1

    # เก็บรายชื่อผู้หารที่เหลือ (อาจเป็น [])
    participants = [p.strip() for p in parts[after_amt_idx:] if p.strip()]

    # Logic สำหรับแยก Payer และ Item
    before_amt = parts[:amt_idx]
    payer = None
    item = None

    if len(before_amt) >= 2:
        payer = before_amt[0].strip()
        item = " ".join(before_amt[1:]).strip()
    elif len(before_amt) == 1:
        payer = (default_payer_name or "").strip() or None
        item = before_amt[0].strip()
    else:
        payer = (default_payer_name or "").strip() or None
        item = None

    if not payer:
        return None, None, None, None, None, None
    if not item:
        item = "ค่าใช้จ่าย"

    return payer, item, amt_val, currency, tag, participants

def extract_amount(text):
    if not text:
        return None
    lines = text.split('\n')
    amount_labels = ['จำนวน', 'amount']
    for i, line in enumerate(lines):
        line_lower = line.lower().strip()
        if any(label in line_lower for label in amount_labels):
            amounts = re.findall(r'(\d{1,3}(?:,\d{3})(?:\.\d{1,2})?)', line)
            for a in amounts:
                try:
                    num = float(a.replace(',', ''))
                    if 1 <= num <= 1000000:
                        return num
                except:
                    continue
        if i + 1 < len(lines):
            next_line = lines[i + 1].strip()
            amounts = re.findall(r'(\d{1,3}(?:,\d{3})(?:\.\d{1,2})?)', next_line)
            for a in amounts:
                try:
                    num = float(a.replace(',', ''))
                    if 1 <= num <= 1000000:
                        return num
                except:
                    continue
    baht_matches = re.findall(r'(\d{1,3}(?:,\d{3})(?:\.\d{1,2})?)\sบาท', text)
    for a in baht_matches:
        try:
            num = float(a.replace(',', ''))
            if 1 <= num <= 1000000:
                return num
        except:
            continue
    return None

def extract_showtime(text):
    if not text:
        return []
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    showtime_list = []
    time_pattern = r'(\d{1,2}[:.]\d{2})\s*[-–]\s*(\d{1,2}[:.]\d{2})'
    for i, line in enumerate(lines):
        time_match = re.search(time_pattern, line)
        if not time_match:
            continue
        time_str = f"{time_match.group(1)}-{time_match.group(2)}".replace('.', ':')
        artist = None
        line_without_time = re.sub(time_pattern, '', line).strip()
        line_without_time = re.sub(r'^[⏱️🎤📋✏️🎵]+\s*', '', line_without_time).strip()
        if line_without_time and len(line_without_time) > 1:
            artist = line_without_time
        if not artist:
            for j in range(i - 1, max(i - 3, -1), -1):
                prev_line = lines[j].strip()
                if re.search(time_pattern, prev_line) or re.match(r'^[\d\s-:.–]+$', prev_line):
                    continue
                prev_clean = re.sub(r'^[⏱️🎤📋✏️🎵]+\s*', '', prev_line).strip()
                if prev_clean and len(prev_clean) > 1:
                    artist = prev_clean
                    break
        if not artist:
            for j in range(i + 1, min(i + 3, len(lines))):
                next_line = lines[j].strip()
                if re.search(time_pattern, next_line) or re.match(r'^[\d\s-:.–]+$', next_line):
                    continue
                next_clean = re.sub(r'^[⏱️🎤📋✏️🎵]+\s*', '', next_line).strip()
                if next_clean and len(next_clean) > 1:
                    artist = next_clean
                    break
        if not artist:
            artist = "Unknown"
        showtime_list.append({"time": time_str, "artist": artist})
    return showtime_list

# =================================================================
# Core DB Functions
# =================================================================
def get_active_trip(user_id, group_id=None):
    if not supabase:
        return None
    try:
        if group_id:
            res = supabase.table("trips").select("*").eq("status", "active").eq("line_group_id", group_id).order("created_at", desc=True).limit(1).execute()
            if res.data:
                return res.data[0]
        res = supabase.table("trips").select("*").eq("status", "active").eq("creator_id", user_id).order("created_at", desc=True).limit(1).execute()
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
    except:
        return user_id[:8]

def get_all_expenses(trip_id):
    if not supabase:
        return []
    try:
        res = supabase.table("expenses").select("*").eq("trip_id", trip_id).order("id", desc=False).execute()
        return res.data if res.data else []
    except:
        return []

def update_expense_amount(expense_id, new_amount):
    if not supabase:
        return False
    try:
        supabase.table("expenses").update({"amount": new_amount}).eq("id", expense_id).execute()
        return True
    except:
        return False

def compute_trip_balances_thb(trip_id, group_id=None):
    expenses = get_all_expenses(trip_id)
    total_thb = 0.0
    paid_totals = {}
    share_totals = {}
    people = set()
    for exp in expenses:
        curr = exp.get("currency", "THB")
        amt_thb = float(exp.get("amount") or 0)
        if curr != "THB":
            amt_thb *= (get_exchange_rate(curr, "THB") or 1.0)
        if amt_thb <= 0:
            continue
        total_thb += amt_thb
        payer = exp.get("payer_name") or get_display_name(exp.get("line_user_id"), group_id)
        if payer:
            paid_totals[payer] = paid_totals.get(payer, 0.0) + amt_thb
            people.add(payer)
        ppl = exp.get("participants") or []
        if isinstance(ppl, str):
            ppl = [p.strip() for p in ppl.split() if p.strip()]
        if not ppl:
            ppl = [payer] if payer else []
        share = amt_thb / max(len(ppl), 1) if ppl else 0.0
        for p in ppl:
            if not p:
                continue
            share_totals[p] = share_totals.get(p, 0.0) + share
            people.add(p)
    return total_thb, paid_totals, share_totals, people

def load_schedules():
    if not supabase:
        return []
    try:
        res = supabase.table("schedules").select("*").order("created_at", desc=True).execute()
        return res.data if res.data else []
    except:
        return []

def get_active_events():
    schedules = load_schedules()
    return [s for s in schedules if s.get('active', True)]

# =================================================================
# Export Excel Functions
# =================================================================
def export_trip_to_excel(trip_id, trip_title):
    try:
        expenses = get_all_expenses(trip_id)
        if not expenses:
            return None, "ไม่มีข้อมูลค่าใช้จ่ายในทริปนี้"
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
                except:
                    date_str, time_str = created[:10], created[11:19]
            ppl = exp.get('participants') or []
            if isinstance(ppl, str):
                ppl = [p.strip() for p in ppl.split() if p.strip()]
            data.append({
                "ชื่อทริป": trip_title, "วันที่": date_str, "เวลา": time_str,
                "ชื่อผู้จ่าย": user_name, "รายการ": exp.get('item_name', ''),
                "จำนวนเงิน": exp.get('amount', 0), "สกุล": exp.get('currency', 'THB'),
                "หมวดหมู่": exp.get('tag', ''), "หาร": " ".join(ppl),
            })
        df = pd.DataFrame(data)
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Expenses')
        output.seek(0)
        return output, None
    except Exception as e:
        return None, str(e)

def upload_excel_to_supabase(file_buffer, filename):
    if not supabase:
        return None, "Supabase client not initialized"
    try:
        supabase.storage.from_("trip-exports").upload(
            path=filename,
            file=file_buffer.getvalue(),
            file_options={"content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "upsert": "true"}
        )
        return supabase.storage.from_("trip-exports").get_public_url(filename), None
    except Exception as e:
        return None, str(e)

# =================================================================
# API Endpoints
# =================================================================
@app.route("/api/event-time", methods=["GET"])
def get_event_time():
    url = request.args.get("url", "")
    if not url:
        return jsonify({"ok": False, "error": "ไม่มี URL"}), 400
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        patterns = [
            r'(\d{1,2})\s+(มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|กรกฎาคม|สิงหาคม|กันยายน|ตุลาคม|พฤศจิกายน|ธันวาคม)\s+(\d{3,4})',
            r'(\d{1,2})[-/](\d{1,2})[-/](\d{4})\s+(\d{1,2}):(\d{2})'
        ]
        matched = ""
        for p in patterns:
            m = re.search(p, resp.text, re.IGNORECASE)
            if m:
                matched = m.group(0)
                break
        return jsonify({"ok": True, "matchedText": matched})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/line-push", methods=["POST"])
def line_push():
    data = request.json
    target_id, message = data.get("targetId"), data.get("message")
    if not target_id or not message:
        return jsonify({"ok": False, "error": "ต้องระบุ targetId และ message"}), 400
    try:
        line_bot_api.push_message(target_id, TextSendMessage(text=message))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/config-status", methods=["GET"])
def config_status():
    return jsonify({
        "ok": True,
        "lineTokenConfigured": bool(os.getenv('LINE_CHANNEL_ACCESS_TOKEN')),
        "lineSecretConfigured": bool(os.getenv('LINE_CHANNEL_SECRET'))
    })

@app.route("/api/schedules", methods=["GET", "POST"])
def handle_schedules():
    if not supabase:
        return jsonify({"ok": False, "error": "Supabase not initialized"}), 500
    if request.method == "GET":
        schedules = load_schedules()
        formatted = [{
            "id": str(s["id"]), "targetId": s.get("target_id", ""), "buyerName": s.get("buyer_name", ""),
            "name": s.get("name", ""), "url": s.get("url", ""), "saleTime": s.get("sale_time", ""),
            "site": s.get("site", ""), "active": s.get("active", True), "createdAt": s.get("created_at", "")
        } for s in schedules]
        return jsonify({"ok": True, "schedules": formatted})
    elif request.method == "POST":
        try:
            data = request.json
            new_s = supabase.table("schedules").insert({
                "target_id": data.get("targetId", ""), "buyer_name": data.get("buyerName", ""),
                "name": data.get("name", ""), "url": data.get("url", ""),
                "sale_time": data.get("saleTime", ""), "site": data.get("site", ""), "active": True
            }).execute()
            if new_s.data:
                ns = new_s.data[0]
                return jsonify({
                    "ok": True, "schedule": {
                        "id": str(ns["id"]), "targetId": ns.get("target_id", ""), "buyerName": ns.get("buyer_name", ""),
                        "name": ns.get("name", ""), "url": ns.get("url", ""), "saleTime": ns.get("sale_time", ""),
                        "site": ns.get("site", ""), "active": ns.get("active", True), "createdAt": ns.get("created_at", "")
                    }
                })
            return jsonify({"ok": False, "error": "Failed to add schedule"}), 500
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/schedules/<schedule_id>", methods=["DELETE"])
def delete_schedule(schedule_id):
    if not supabase:
        return jsonify({"ok": False, "error": "Supabase not initialized"}), 500
    try:
        supabase.table("schedules").delete().eq("id", schedule_id).execute()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/server-time", methods=["GET"])
def get_server_time():
    import time
    return jsonify({"ok": True, "serverTime": int(time.time() * 1000)})

@app.route("/api/check-showtime", methods=["POST"])
def check_showtime_cron():
    """Cron job ตรวจสอบและแจ้งเตือน Showtime - แก้ไข Logic เวลาและ Auto-End"""
    auth = request.headers.get("Authorization", "")
    secret = os.getenv("CRON_SECRET", "")
    if secret and auth != f"Bearer {secret}":
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    # ใช้เวลาไทย (UTC+7)
    now = datetime.now() + timedelta(hours=7)
    ended = 0
    alerted = 0

    showtime = load_showtime()
    schedule = showtime.get("schedule", []) or []

    def _start_hhmm(t):
        """แยกเวลาเริ่มต้นจาก time range และจัดรูปแบบเป็น HH:MM"""
        if not t:
            return None
        s = str(t).split("-")[0].strip().replace(".", ":").strip()
        if re.fullmatch(r"\d{1,2}:\d{2}", s):
            h, m = s.split(":")
            return f"{int(h):02d}:{int(m):02d}"
        return None

    # วนลูปตรวจสอบ state ของผู้ใช้
    for uid, st in list(user_state.items()):
        if st.get("action") != "showtime_mode":
            continue

        # 1. ตรวจสอบ Auto-End เมื่อเลยวันที่กำหนด
        end_date = st.get("end_date")
        if end_date:
            try:
                end_dt = datetime.strptime(end_date, "%Y-%m-%d")
                # เปรียบเทียบเฉพาะวันที่ ไม่ต้องสนเวลา (จบทันทีเมื่อข้ามวัน)
                if now.date() > end_dt.date():
                    target = st.get("target_id") or uid
                    clear_state(uid)
                    try:
                        line_bot_api.push_message(target, TextSendMessage(text="🛑 Auto-End: Showtime หมดวันที่ตั้งไว้แล้ว"))
                    except Exception as e:
                        logger.error(f"Auto-end push error: {e}")
                    ended += 1
                    continue
            except Exception:
                pass

        # 2. ตรวจสอบเวลาแจ้งเตือน
        current_hhmm = now.strftime("%H:%M")

        for item in schedule:
            start = _start_hhmm(item.get("time"))
            if not start or start != current_hhmm:
                continue

            # ป้องกันการแจ้งเตือนซ้ำในนาทีเดิม
            key = f"{now.strftime('%Y-%m-%d')}|{start}|{item.get('artist','')}"
            if st.get("last_alert_key") == key:
                continue

            target = st.get("target_id") or uid
            try:
                artist = item.get("artist", "-")
                time_range = item.get("time", start)
                line_bot_api.push_message(target, TextSendMessage(text=f"🎤 Showtime Now: {artist}\n⏱️ {time_range}"))
                st["last_alert_key"] = key
                alerted += 1
            except Exception as e:
                logger.error(f"Showtime alert push error: {e}")
            break

    return jsonify({"ok": True, "ended": ended, "alerted": alerted, "serverTime": now.isoformat()})

@app.route("/api/daily-summary", methods=["POST"])
def daily_summary_cron():
    """Cron Job สำหรับสรุปยอดรายวัน Multi-Currency"""
    if not supabase:
        return jsonify({"ok": False, "error": "Supabase not initialized"}), 500

    auth = request.headers.get("Authorization", "")
    secret = os.getenv("CRON_SECRET", "")
    if secret and auth != f"Bearer {secret}":
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    try:
        today_str = (datetime.now() + timedelta(hours=7)).strftime("%Y-%m-%d")
        trips_res = supabase.table("trips").select("*").eq("status", "active").execute()
        trips = trips_res.data if trips_res.data else []
        sent_count = 0

        for trip in trips:
            expenses = get_all_expenses(trip['id'])
            today_exp = [e for e in expenses if e.get('created_at', '').startswith(today_str)]
            if not today_exp:
                continue

            total_thb = 0.0
            currency_totals = {}
            categories = {}

            for exp in today_exp:
                curr = exp.get('currency', 'THB')
                raw_amount = float(exp.get('amount') or 0)
                currency_totals[curr] = currency_totals.get(curr, 0.0) + raw_amount
                amt_thb = raw_amount
                if curr != 'THB':
                    rate = get_exchange_rate(curr, 'THB')
                    amt_thb *= (rate or 1.0)
                total_thb += amt_thb

                tag = exp.get('tag') or '#ทั่วไป'
                if tag not in categories:
                    categories[tag] = {'total_thb': 0.0, 'participants': set()}
                categories[tag]['total_thb'] += amt_thb

                ppl = exp.get('participants') or []
                if isinstance(ppl, str):
                    ppl = [p.strip() for p in ppl.split() if p.strip()]
                if not ppl:
                    payer = exp.get('payer_name') or get_display_name(exp['line_user_id'], trip.get('line_group_id'))
                    ppl = [payer]
                for p in ppl:
                    if p:
                        categories[tag]['participants'].add(str(p))

            msg = f"📊 สรุปยอดประจำวัน ({today_str})\n🚀 ทริป: {trip['title']}\n\n"
            if currency_totals:
                cur_lines = [f"{c} {float(v):,.2f}" for c, v in sorted(currency_totals.items())]
                msg += "💱 ยอดตามสกุล: " + " | ".join(cur_lines) + "\n"
            msg += f"💵 ยอดรวมวันนี้ (THB): {total_thb:,.2f} บาท\n\n"
            for tag, data in sorted(categories.items()):
                ppl_str = " ".join(sorted(data['participants']))
                msg += f"{tag} {data['total_thb']:,.0f} บ. ({ppl_str})\n"

            target = trip.get('line_group_id') or trip.get('creator_id')
            if target:
                line_bot_api.push_message(target, TextSendMessage(text=msg))
                sent_count += 1

            details = {tag: {"total_thb": d['total_thb'], "participants": list(d['participants'])} for tag, d in categories.items()}
            try:
                supabase.table("daily_summaries").upsert({
                    "trip_id": trip['id'], "summary_date": today_str,
                    "total_thb": total_thb, "details": details
                }, on_conflict="trip_id,summary_date").execute()
            except Exception as db_err:
                logger.warning(f"Failed to save daily summary to DB: {db_err}")

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
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    text = event.message.text.strip()
    text_lower = text.lower()
    user_id = event.source.user_id
    group_id = getattr(event.source, 'group_id', None)
    reply_token = event.reply_token
    state = get_state(user_id)

    # --- BLOCK 1: Handle Follow-up states for Slip Payer & Participants ---
    if state and state.get("action") == "wait_slip_payer":
        payer_name = text
        if payer_name in ["ฉัน", "ฉันเอง", "me"]:
            payer_name = get_display_name(user_id, state.get('group_id'))
        set_state(user_id, {
            "action": "wait_slip_participants",
            "message_id": state["message_id"],
            "trip_id": state["trip_id"],
            "group_id": state.get("group_id"),
            "payer_name": payer_name,
        })
        line_bot_api.reply_message(reply_token, TextSendMessage(text="👥 หารกับใครบ้าง?\nพิมพ์ชื่อคั่นด้วยเว้นวรรค เช่น: บอล ปาค เอ็ม"))
        return

    if state and state.get("action") == "wait_slip_participants":
        names = [n.strip() for n in text.split() if n.strip()]
        if not names:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาพิมพ์รายชื่ออย่างน้อย 1 คน"))
            return
        my_name = get_display_name(user_id, state.get('group_id'))
        names = [my_name if n in ["ฉัน", "ฉันเอง", "me"] else n for n in names]
        payer_name = state.get("payer_name")
        threading.Thread(target=process_slip_with_payer, args=(
            state["message_id"], state["trip_id"], user_id,
            state.get("group_id"), reply_token, payer_name, names
        )).start()
        clear_state(user_id)
        return

    # เพิ่มบล็อกใหม่สำหรับข้อความปกติ (ไม่ใช่สลิป)
    if state and state.get("action") == "wait_expense_participants":
        names = [n.strip() for n in text.split() if n.strip()]
        if not names:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาพิมพ์รายชื่ออย่างน้อย 1 คน"))
            return
        my_name = get_display_name(user_id, state.get("group_id"))
        names = [my_name if n in ["ฉัน", "ฉันเอง", "me"] else n for n in names]
        if not supabase:
            return
        try:
            supabase.table("expenses").insert({
                "trip_id": state["trip_id"], "line_user_id": user_id,
                "payer_name": state["payer_name"],
                "amount": state["amount"], "item_name": state["item"],
                "currency": state["currency"], "tag": state["tag"], "participants": names, "slip_url": None
            }).execute()
            curr_txt = f" {state['currency']}" if state['currency'] != "THB" else ""
            tag_txt = f" ({state['tag']})" if state['tag'] else ""
            ppl_txt = " ".join(names)
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ บันทึก {state['amount']:,.2f}{curr_txt}{tag_txt}\nจ่าย: {state['payer_name']}\nหาร: {ppl_txt}"))
        except Exception as e:
            logger.error(f"Save expense error: {e}")
        clear_state(user_id)
        return

    # --- BLOCK 2: Normal Commands ---
    if text_lower == "showtime":
        if state and state.get("action") == "showtime_mode":
            line_bot_api.reply_message(reply_token, [TextSendMessage(text=format_showtime_message()), build_showtime_menu_flex(state.get("end_date"))])
            return
        set_state(user_id, {"action": "wait_showtime_date"})
        line_bot_api.reply_message(reply_token, TextSendMessage(text="📅 กรุณาระบุวันที่สิ้นสุด Showtime (YYYY-MM-DD)\nเช่น 2026-05-30\n(พิมพ์ 'ข้าม' หากไม่ต้องการกำหนด)"))
        return

    if state and state.get("action") == "wait_showtime_date":
        end_date = None
        if text_lower != "ข้าม" and text_lower != "skip":
            try:
                parsed = datetime.strptime(text.strip(), "%Y-%m-%d")
                end_date = parsed.strftime("%Y-%m-%d")
            except ValueError:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ รูปแบบวันที่ไม่ถูกต้อง กรุณาใช้ YYYY-MM-DD หรือพิมพ์ 'ข้าม'"))
                return
        set_state(user_id, {
            "action": "showtime_mode", "end_date": end_date, "edit_mode": False,
            "target_id": group_id or user_id, "group_id": group_id, "last_alert_key": None,
        })
        msg = format_showtime_message() + "\n\n📸 ส่งรูป Showtime ใหม่เพื่ออัปเดต"
        line_bot_api.reply_message(reply_token, [TextSendMessage(text=msg), build_showtime_menu_flex(end_date)])
        return

    if text_lower in ["end showtime", "stop showtime", "จบ showtime"]:
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

    if text_lower in ["editshowtime"]:
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
            existing["schedule"] = sort_showtime_by_time(schedule)
            existing["last_updated"] = datetime.now().isoformat()
            save_showtime(existing)
            line_bot_api.reply_message(reply_token, TextSendMessage(text="✅ อัปเดต Showtime เสร็จ!\n\n" + format_showtime_message() + "\n\n📝 พิมพ์เพิ่มเติม หรือ 'save' เพื่อสิ้นสุด"))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่พบรูปแบบที่ถูกต้อง\n👉 ตัวอย่าง: 13:00-13:50 ROMANCE"))
        return

    if text in ["เมนู", "menu", "help"]:
        if state and state.get("action") == "showtime_mode":
            line_bot_api.reply_message(reply_token, build_showtime_menu_flex(state.get("end_date")))
        else:
            line_bot_api.reply_message(reply_token, build_main_menu_flex())
        return

    if state and state.get("action") == "showtime_mode":
        allowed = ["save", "showtime", "editshowtime", "update showtime", "เมนู", "menu", "help", "ยกเลิก", "cancel", "end showtime", "stop showtime"]
        if text_lower not in allowed and text not in allowed:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⏸️ ตอนนี้อยู่ในโหมด Showtime\nพิมพ์ 'menu' เพื่อดูคำสั่ง"))
            return

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
                        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ แก้ไขรายการ ID {inline_id:04d} ({selected['item_name'][:30]}) จาก {selected['amount']:,.2f} บาท เป็น {inline_amount:,.2f} บาท เรียบร้อย!"))
                    else:
                        line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ แก้ไขไม่สำเร็จ"))
                else:
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"⚠️ ไม่พบรายการ ID {inline_id:04d}"))
                return
            except:
                pass
        expenses = get_all_expenses(trip['id'])
        if not expenses:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่มีรายการค่าใช้จ่ายให้แก้ไข"))
            return
        if state:
            clear_state(user_id)
        msg = "✏️ เลือกรายการที่ต้องการแก้ไขยอดเงิน (พิมพ์ ID 4 หลัก):\n=======================\n"
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
                    set_state(user_id, {
                        "action": "edit_amount", "expense_id": selected['id'],
                        "expense_item": selected['item_name'], "old_amount": selected['amount']
                    })
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✏️ แก้ไขรายการ ID {selected['id']:04d}: {selected['item_name'][:50]}\n💰 ยอดเดิม: {selected['amount']:,.2f} บาท\n\n👉 พิมพ์จำนวนเงินใหม่"))
                else:
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"⚠️ ไม่พบรายการ ID {expense_id}"))
                    clear_state(user_id)
            elif len(parts) >= 2:
                expense_id, new_amount = int(parts[0]), float(parts[1].replace(',', ''))
                selected = next((e for e in expenses if e['id'] == expense_id), None)
                if selected and new_amount > 0 and update_expense_amount(selected['id'], new_amount):
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ แก้ไขรายการ ID {selected['id']:04d} จาก {selected['amount']:,.2f} บาท เป็น {new_amount:,.2f} บาท เรียบร้อย!"))
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
            if new_amount <= 0:
                raise ValueError
            eid = state["expense_id"]
            old = state["old_amount"]
            item = state["expense_item"]
            if update_expense_amount(eid, new_amount):
                line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ แก้ไขรายการ ID {eid:04d} ({item[:30]}) จาก {old:,.2f} บาท เป็น {new_amount:,.2f} บาท เรียบร้อย!"))
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ แก้ไขไม่สำเร็จ"))
        except:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาพิมพ์จำนวนเงินเป็นตัวเลข"))
        clear_state(user_id)
        return

    if text == "id":
        msg = f"🔑 User ID: {user_id}"
        if group_id:
            msg += f"\n👥 Group ID: {group_id}"
        else:
            msg += "\nℹ️ แชทนี้เป็น DM"
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return

    if text.startswith("ทริป ") or text_lower.startswith("trip "):
        trip_name = text[5:].strip() if text.startswith("ทริป ") else text[5:].strip()
        if not trip_name:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาระบุชื่อทริป"))
            return
        if not supabase:
            return
        try:
            supabase.table("trips").update({"status": "closed"}).eq("creator_id", user_id).execute()
            supabase.table("trips").insert({
                "title": trip_name, "status": "active",
                "line_group_id": group_id, "creator_id": user_id
            }).execute()
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"🚀 เริ่มทริปใหม่: {trip_name} เรียบร้อย!"))
        except Exception as e:
            logger.error(f"Create trip error: {e}")
            line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ สร้างทริปไม่สำเร็จ"))
        return

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
            if curr != 'THB':
                amt_thb *= (get_exchange_rate(curr, 'THB') or 1.0)
            total_thb += amt_thb
            tag = exp.get('tag') or '#ทั่วไป'
            if tag not in categories:
                categories[tag] = {'total': 0, 'participants': set()}
            categories[tag]['total'] += amt_thb
            ppl = exp.get('participants') or []
            if isinstance(ppl, str):
                ppl = [p.strip() for p in ppl.split() if p.strip()]
            if not ppl:
                payer = exp.get('payer_name') or get_display_name(exp['line_user_id'], group_id)
                ppl = [payer]
            for p in ppl:
                if p:
                    categories[tag]['participants'].add(str(p))
        lines = [f"ยอดรวม {total_thb:,.2f}"]
        for tag, data in sorted(categories.items()):
            ppl_str = " ".join(sorted(data['participants']))
            lines.append(f"{tag} {data['total']:,.0f} {ppl_str}")
        line_bot_api.reply_message(reply_token, TextSendMessage(text="\n".join(lines)))
        return

    if text == "ยอดวันนี้" or text_lower == "ยอดวันนี้" or text_lower.startswith("ยอดวันนี้ "):
        parts = text.split()
        target_curr = "THB"
        if len(parts) > 1:
            t = _normalize_currency(parts[1])
            target_curr = "JPY" if t == "JYP" else (t or parts[1].upper())
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
        total_thb = 0
        categories = {}
        currency_totals = {}
        for exp in today_exp:
            curr = exp.get('currency', 'THB')
            currency_totals[curr] = currency_totals.get(curr, 0) + (exp.get('amount') or 0)
            amt_thb = exp['amount']
            if curr != 'THB':
                amt_thb *= (get_exchange_rate(curr, 'THB') or 1.0)
            total_thb += amt_thb
            tag = exp.get('tag') or '#ทั่วไป'
            if tag not in categories:
                categories[tag] = {'total_thb': 0, 'participants': set()}
            categories[tag]['total_thb'] += amt_thb
            ppl = exp.get('participants') or []
            if isinstance(ppl, str):
                ppl = [p.strip() for p in ppl.split() if p.strip()]
            if not ppl:
                payer = exp.get('payer_name') or get_display_name(exp['line_user_id'], group_id)
                ppl = [payer]
            for p in ppl:
                if p:
                    categories[tag]['participants'].add(str(p))
        rate_to_target = get_exchange_rate("THB", target_curr) if target_curr != "THB" else 1.0
        total_target = total_thb * rate_to_target
        subtitle_parts = []
        if currency_totals:
            cur_lines = [f"{c} {float(v):,.2f}" for c, v in sorted(currency_totals.items())]
            subtitle_parts.append("💱 ยอดตามสกุล: " + " | ".join(cur_lines))
        if target_curr != "THB":
            subtitle_parts.append(f"1 THB = {rate_to_target:.4f} {target_curr}")
            subtitle_parts.append(f"💵 รวมวันนี้: {total_thb:,.2f} บาท (≈ {total_target:,.2f} {target_curr})")
        else:
            subtitle_parts.append(f"💵 รวมวันนี้: {total_thb:,.2f} บาท")
        lines = []
        for tag, data in sorted(categories.items()):
            ppl_str = " ".join(sorted(data['participants']))
            line = f"{tag} {data['total_thb']:,.0f} {ppl_str}"
            if target_curr != "THB":
                converted = data['total_thb'] * rate_to_target
                line += f" (≈ {converted:,.2f} {target_curr})"
            lines.append(line)
        line_bot_api.reply_message(reply_token, build_report_flex(
            title=f"📅 ยอดวันนี้ ({today_str})",
            subtitle="\n".join(subtitle_parts),
            lines=lines,
            alt_text="ยอดวันนี้"
        ))
        return

    if text.startswith("จบทริป ") or text_lower.startswith("end trip "):
        trip = get_active_trip(user_id, group_id)
        if not trip:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่มีทริปที่กำลังทำงานอยู่"))
            return
        currency_code = "THB"
        if text.startswith("จบทริป "):
            parts = text.split()
            if len(parts) >= 2:
                currency_code = parts[1].upper()
        elif text_lower.startswith("end trip "):
            parts = text_lower.split()
            if len(parts) >= 3:
                currency_code = parts[2].upper()
        set_state(user_id, {
            "action": "end_trip", "trip_id": trip['id'],
            "trip_title": trip['title'], "currency_code": currency_code
        })
        if currency_code != "THB":
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"🏁 ปิดทริป: {trip['title']}\n💱 แปลงเป็นสกุล: {currency_code}\n\n👥 ระบุจำนวนคนที่จะหาร: "))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"🏁 ปิดทริป: {trip['title']}\n\n👥 ระบุจำนวนคนที่จะหาร: "))
        return

    if state and state.get("action") == "end_trip":
        try:
            num_people = int(text)
            if num_people <= 0:
                raise ValueError
        except:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาระบุจำนวนคนเป็นตัวเลขที่มากกว่า 0"))
            return
        trip_id = state["trip_id"]
        trip_title = state["trip_title"]
        currency_code = state.get("currency_code", "THB")
        total_thb, paid_totals, share_totals, people = compute_trip_balances_thb(trip_id, group_id)
        if total_thb == 0:
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"🚀 ทริป: {trip_title}\n⚠️ ไม่มีรายการค่าใช้จ่ายให้หาร"))
            clear_state(user_id)
            return
        real_people = sorted(list(people)) if people else []
        real_n = len(real_people) if real_people else num_people
        avg = total_thb / max(real_n, 1)
        exchange_rate = 1
        if currency_code != "THB":
            rate = get_exchange_rate("THB", currency_code)
            if rate:
                exchange_rate = rate
            else:
                currency_code = "THB"
        msg = f"🚀 ทริป: {trip_title}\n👥 จำนวนคน: {real_n}\n\n"
        if real_people and num_people != real_n:
            msg += f"⚠️ จำนวนคนที่คุณพิมพ์ ({num_people}) ไม่ตรงกับที่พบในรายการ ({real_n})\n\n"
        if currency_code != "THB" and exchange_rate != 1:
            msg += f"💱 อัตราแลกเปลี่ยน: 1 THB = {exchange_rate:.4f} {currency_code}\n\n"
            msg += f"📉 ยอดหารเฉลี่ย:\n   • {avg:,.2f} บาท/คน\n   • ≈ {avg * exchange_rate:,.2f} {currency_code}/คน\n\n"
        else:
            msg += f"📉 ยอดหารเฉลี่ย: {avg:,.2f} บาท/คน\n\n"
        msg += "💵 ยอดสรุปสุทธิ (จ่ายเพิ่ม/รับคืน):\n"
        for name in real_people:
            paid = paid_totals.get(name, 0.0)
            share = share_totals.get(name, 0.0)
            diff = paid - share
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
        try:
            if supabase:
                supabase.table("trips").update({"status": "closed", "currency_code": currency_code}).eq("id", trip_id).execute()
        except Exception as e:
            logger.error(f"Close trip error: {e}")
        clear_state(user_id)
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return

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
        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ สร้างไฟล์ Excel สำเร็จ!\n📊 ทริป: {trip['title']}\n🔗 {public_url}"))
        return

    if text in ["ประวัติ", "history"]:
        if not supabase:
            return
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
                line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาพิมพ์ตัวเลขเท่านั้น เช่น excel 1"))
                return
            except Exception as e:
                logger.error(f"Export history error: {e}")
                line_bot_api.reply_message(reply_token, TextSendMessage(text=f"❌ เกิดข้อผิดพลาด: {str(e)}"))
            clear_state(user_id)
            return
        else:
            clear_state(user_id)

    if text_lower == "event":
        events = get_active_events()
        base_url = "https://line-chat-bot-trip-manager.onrender.com"
        if not events:
            msg = f"🔍 ตรวจสอบรายชื่อคิว Event ปัจจุบัน...\n=======================\nℹ️ ไม่มีคิว Event ที่เปิดอยู่ (หรือทุกงานหมดอายุ/ถูกปิดแล้ว)\n-----------------------\n\n💻 ลิงก์ควบคุมแผงระบบ:\n{base_url}"
        else:
            msg = "🔍 ตรวจสอบรายชื่อคิว Event ปัจจุบัน...\n=======================\n"
            for i, e in enumerate(events, 1):
                msg += f"{i}. งาน: {e.get('name', '-')}\n⏰ เวลาขาย: {e.get('saleTime', '-')}\n🔗 ลิงก์งาน: {e.get('url', '-')}\n-----------------------\n"
            msg += f"\n💻 ลิงก์ควบคุมแผงระบบ:\n{base_url}"
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return

    if text_lower == "stop event":
        events = get_active_events()
        if not events:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่มี Event ที่กำลังทำงานอยู่"))
            return
        set_state(user_id, {"action": "stop_event", "events": events})
        msg = "🚫 เลือกหมายเลข Event ที่คุณต้องการสั่งหยุดทำงาน (Stop):\n=======================\n"
        for i, e in enumerate(events, 1):
            msg += f"{i}. งาน: {e.get('name', '-')}\n🛑 (ID: {e.get('id', '-')})\n-----------------------\n"
        msg += "👉 พิมพ์เฉพาะ [ตัวเลขลำดับ] เพื่อระบุเลือกปิดงานชิ้นนั้นได้เลยครับ"
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return

    if state and state.get("action") == "stop_event":
        if not supabase:
            return
        try:
            choice = int(text_lower) - 1
            events = state["events"]
            if 0 <= choice < len(events):
                selected = events[choice]
                supabase.table("schedules").update({"active": False}).eq("id", selected.get('id')).execute()
                line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ สั่งปิดงานเรียบร้อยแล้ว!\n🛑 สั่งหยุดภารกิจงาน: {selected.get('name', '-')}\nสถานะคิวเตือนถูกระงับถาวรเรียบร้อย"))
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ หมายเลขไม่ถูกต้อง กรุณาลองใหม่"))
        except ValueError:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาพิมพ์หมายเลขเท่านั้น"))
        except Exception as e:
            logger.error(f"Stop event error: {e}")
            line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ เกิดข้อผิดพลาด กรุณาลองใหม่"))
        clear_state(user_id)
        return

    # --- BLOCK 3: Enhanced Expense Parsing ---
    trip = get_active_trip(user_id, group_id)
    if trip and not (state and state.get("action") == "showtime_mode"):
        default_payer = get_display_name(user_id, group_id)
        payer, item, amount, currency, tag, participants = parse_enhanced_expense(text, default_payer_name=default_payer)
        if amount and amount > 0:
            if not participants:
                set_state(user_id, {
                    "action": "wait_expense_participants",
                    "trip_id": trip['id'], "group_id": group_id,
                    "payer_name": payer,
                    "item": item, "amount": amount,
                    "currency": currency, "tag": tag
                })
                line_bot_api.reply_message(reply_token, TextSendMessage(text="👥 หารกับใครบ้าง?\nพิมพ์ชื่อคั่นด้วยเว้นวรรค เช่น: บอล ปาค เอ็ม"))
                return
            if not supabase:
                return
            try:
                supabase.table("expenses").insert({
                    "trip_id": trip['id'], "line_user_id": user_id,
                    "payer_name": payer,
                    "amount": amount, "item_name": item or "ค่าใช้จ่าย",
                    "currency": currency, "tag": tag, "participants": participants, "slip_url": None
                }).execute()
                curr_txt = f" {currency}" if currency != "THB" else ""
                tag_txt = f" ({tag})" if tag else ""
                ppl_txt = " ".join(participants or [])
                line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ บันทึก {amount:,.2f}{curr_txt}{tag_txt}\nจ่าย: {payer}\nหาร: {ppl_txt}"))
            except Exception as e:
                logger.error(f"Save expense error: {e}")
        return

@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    user_id = event.source.user_id
    group_id = getattr(event.source, 'group_id', None)
    reply_token = event.reply_token
    state = get_state(user_id)

    if state and state.get("action") == "showtime_mode":
        if not vision_client:
            return
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
                            if old_item["time"] == new_item["time"]:
                                schedule[i] = new_item
                                found = True
                                break
                        if not found:
                            schedule.append(new_item)
                    existing["schedule"] = sort_showtime_by_time(schedule)
                    existing["last_updated"] = datetime.now().isoformat()
                    save_showtime(existing)
                    msg = "✅ อัปเดต Showtime เสร็จ!\n\n" + format_showtime_message() + "\n\n📝 พิมพ์เพิ่มเติม หรือ 'save'"
                else:
                    state["showtime_temp"] = showtime_list
                    msg = "✅ อ่านข้อมูล Showtime สำเร็จ\n\n📋 **ตารางการแสดง:**\n\n"
                    for item in showtime_list:
                        msg += f"⏱️ {item['time']} | 🎤 {item['artist']}\n"
                    msg += "\n👉 พิมพ์ 'save' เพื่อบันทึก"
                line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่พบข้อมูล Showtime ในรูป"))
        except Exception as e:
            logger.error(f"Showtime OCR error: {e}")
            line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ ไม่สามารถอ่านรูปได้"))
        return

    trip = get_active_trip(user_id, group_id)
    if not trip:
        line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่มีทริปที่กำลังทำงานอยู่"))
        return
    set_state(user_id, {
        "action": "wait_slip_payer", "message_id": event.message.id,
        "trip_id": trip['id'], "group_id": group_id
    })
    line_bot_api.reply_message(reply_token, TextSendMessage(text="🧾 พบสลิป/บิล\n👤 กรุณาพิมพ์ชื่อคนที่ต้องรับผิดชอบยอดนี้\n(หรือพิมพ์ 'ฉัน' เพื่อใช้ชื่อคุณ)"))

def process_slip_with_payer(message_id, trip_id, user_id, group_id, reply_token, payer_name, participants):
    if not vision_client or not supabase:
        return
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
                "amount": amount, "item_name": f"บิล {timestamp}", "currency": "THB",
                "tag": "#สลิป", "participants": participants,
                "slip_url": f"slip_{message_id}"
            }).execute()
            new_id = result.data[0]['id'] if result.data else None
            ppl_txt = " ".join(participants or [])
            success_msg = f"✅ บันทึก {amount:,.2f} บาท (#สลิป)\nจ่าย: {payer_name}\nหาร: {ppl_txt}"
            if new_id:
                success_msg += f"\n✏️ แก้ไข: edit {new_id:04d} {amount}"
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