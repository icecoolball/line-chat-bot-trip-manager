import os
import re
import json
import logging
import threading
import traceback
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

# --- Configuration ---
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))

creds_json = os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON')
if creds_json:
    try:
        creds_dict = json.loads(creds_json)
        creds = service_account.Credentials.from_service_account_info(creds_dict)
        vision_client = vision.ImageAnnotatorClient(credentials=creds)
        logger.info(
            "Vision credential loaded: project_id=%s client_email=%s private_key_id=%s",
            creds_dict.get("project_id"),
            creds_dict.get("client_email"),
            creds_dict.get("private_key_id"),
        )
    except Exception as e:
        logger.error("Invalid GOOGLE_APPLICATION_CREDENTIALS_JSON: %s", e)
        logger.error(traceback.format_exc())
        vision_client = None
else:
    logger.error("โ GOOGLE_APPLICATION_CREDENTIALS_JSON missing")
    vision_client = None

supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_ANON_KEY")
if supabase_url and supabase_key:
    supabase: Client = create_client(supabase_url, supabase_key)
else:
    logger.error("โ Supabase environment variables missing")
    supabase = None

user_state = {}
STATE_TIMEOUT_SECONDS = 600
SHOWTIME_ACTIONS = {
    "showtime_mode",
    "wait_showtime_add_confirm",
    "wait_showtime_event_name",
    "wait_showtime_date",
    "wait_end_showtime_event_index",
    "wait_end_showtime_confirm",
}

# =================================================================
# State Management
# =================================================================
def set_state(user_id, data):
    data["_ts"] = datetime.now().timestamp()
    user_state[user_id] = data

def get_state(user_id):
    s = user_state.get(user_id)
    if not s: return None
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
                    ButtonComponent(style='primary', color='#1DB446', height='md', action=MessageAction(label='🚀 สร้างทริป', text='ทริป')),
                    ButtonComponent(style='secondary', height='md', action=MessageAction(label='💰 ยอดรวม', text='ยอด')),
                    ButtonComponent(style='secondary', height='md', action=MessageAction(label='✏️ แก้ไข', text='edit')),
                    ButtonComponent(style='secondary', height='md', action=MessageAction(label='🎤 Showtime', text='showtime')),
                    ButtonComponent(style='secondary', height='md', action=MessageAction(label='📋 เมนู', text='เมนู'))
                ]
            )
        )
    )

def build_showtime_menu_flex(show_date=None):
    info_text = f"📅 วันที่จัดแสดง: {show_date}" if show_date else "ไม่ได้กำหนดวันที่จัดแสดง"
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
                    ButtonComponent(style='secondary', height='md', action=MessageAction(label='✏️ แก้ไขข้อความ', text='edit showtime')),
                    ButtonComponent(style='primary', color='#FF5551', height='md', action=MessageAction(label='🛑 จบ Showtime', text='end showtime')),
                    ButtonComponent(style='secondary', color='#888888', height='md', action=MessageAction(label='↩ ออก (Exit)', text='exit'))
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
def load_showtime_event_meta(event_name):
    if not supabase or not event_name:
        return {}
    try:
        res = supabase.table("showtime_events").select("*").eq("event_name", event_name).limit(1).execute()
        rows = res.data if res.data else []
        return rows[0] if rows else {}
    except Exception as e:
        logger.error(f"Load showtime event meta error: {e}")
        return {}

def save_showtime_event_meta(event_name, show_date=None):
    if not supabase or not event_name:
        return False
    try:
        payload = {"event_name": event_name, "show_date": show_date}
        supabase.table("showtime_events").upsert(payload, on_conflict="event_name").execute()
        return True
    except Exception as e:
        logger.error(f"Save showtime event meta error: {e}")
        return False

def load_showtime(event_name=None):
    if not supabase: return {"schedule": [], "last_updated": None}
    try:
        query = supabase.table("showtimes").select("*")
        if event_name:
            query = query.eq("event_name", event_name)
        res = query.execute()
        schedule = res.data if res.data else []
        meta = load_showtime_event_meta(event_name) if event_name else {}
        return {
            "event_name": event_name,
            "show_date": meta.get("show_date"),
            "schedule": schedule,
            "last_updated": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Load showtime error: {e}")
        return {"event_name": event_name, "show_date": None, "schedule": [], "last_updated": None}

def save_showtime(showtime_data, event_name):
    if not supabase: return False
    try:
        show_date = showtime_data.get("show_date")
        if not save_showtime_event_meta(event_name, show_date):
            return False
        supabase.table("showtimes").delete().eq("event_name", event_name).execute()
        schedule = showtime_data.get("schedule", [])
        for item in schedule:
            supabase.table("showtimes").insert({
                "time": item.get("time", ""),
                "artist": item.get("artist", ""),
                "event_name": event_name
            }).execute()
        return True
    except Exception as e:
        logger.error(f"Save showtime error: {e}")
        return False

def list_showtime_events():
    if not supabase: return []
    try:
        res = supabase.table("showtimes").select("event_name").execute()
        rows = res.data if res.data else []
        meta_map = {}
        try:
            meta_res = supabase.table("showtime_events").select("event_name,show_date").execute()
            meta_rows = meta_res.data if meta_res.data else []
            for row in meta_rows:
                name = (row.get("event_name") or "").strip()
                if name:
                    meta_map[name] = row.get("show_date")
        except Exception as meta_err:
            logger.warning(f"List showtime event meta error: {meta_err}")
        counts = {}
        for row in rows:
            name = (row.get("event_name") or "default").strip() or "default"
            counts[name] = counts.get(name, 0) + 1
        events = []
        all_names = set(counts.keys()) | set(meta_map.keys())
        for name in sorted(all_names, key=lambda x: x.lower()):
            events.append({
                "event_name": name,
                "count": counts.get(name, 0),
                "show_date": meta_map.get(name)
            })
        return events
    except Exception as e:
        logger.error(f"List showtime events error: {e}")
        return []

def delete_showtime_event(event_name):
    if not supabase: return False
    try:
        supabase.table("showtimes").delete().eq("event_name", event_name).execute()
        supabase.table("showtime_events").delete().eq("event_name", event_name).execute()
        return True
    except Exception as e:
        logger.error(f"Delete showtime event error: {e}")
        return False

def build_showtime_event_list_message():
    events = list_showtime_events()
    if not events:
        return "ยังไม่มีตาราง Showtime ในระบบ"
    msg = "ตาราง Showtime ที่มีอยู่:\n"
    for i, ev in enumerate(events, 1):
        date_text = f" | {ev['show_date']}" if ev.get("show_date") else ""
        msg += f"{i}. {ev['event_name']} ({ev['count']} วง){date_text}\n"
    return msg

def build_showtime_command_text():
    return (
        "📋 เมนู Showtime:\n"
        "- menu showtime\n"
        "- state\n"
        "- showtime\n"
        "- showtime [เลข]\n"
        "- เพิ่ม\n"
        "- add showtime\n"
        "- end showtime\n"
        "- save\n"
        "- exit\n"
        "- cancel"
    )

def sort_showtime_by_time(schedule):
    def get_sort_key(item):
        time_str = item.get("time", "00:00").split('-')[0]
        try:
            h, m = map(int, time_str.split(':'))
            return (1, h * 60 + m) if 0 <= h < 9 else (0, h * 60 + m)
        except: return (2, 0)
    return sorted(schedule, key=get_sort_key)

def format_showtime_message(show_date=None, event_name=None):
    showtime = load_showtime(event_name)
    if not show_date:
        show_date = showtime.get("show_date")
    show_date_text = show_date if show_date else "ไม่ระบุ"
    date_header = f"📅 วันที่จัดแสดง: {show_date_text}\n"
    if not showtime.get("schedule"): return f"{date_header}ℹ️ ยังไม่มีข้อมูล Showtime"
    sorted_schedule = sort_showtime_by_time(showtime.get("schedule", []))
    msg = f"{date_header}📋 ตารางการแสดง:\n\n"
    for item in sorted_schedule:
        time_display = item.get('time', '-').replace('.', ':')
        artist = item.get('artist', '-')
        msg += f"⏱️ {time_display} | 🎤 {artist}\n"
    return msg

def build_state_text(state):
    if state and state.get("action") in SHOWTIME_ACTIONS:
        return "🎤 ตอนนี้อยู่ในโหมด Showtime"
    return "📋 ตอนนี้อยู่ในโหมดปกติ"

# =================================================================
# Currency & Expense Helpers
# =================================================================
CURRENCY_RATES = {"THB": 1, "JPY": 0.23, "USD": 34.5, "KRW": 0.025}
_RATE_CACHE = {}
_RATE_CACHE_TTL_SECONDS = 1800

def get_exchange_rate(from_curr, to_curr):
    if from_curr == to_curr: return 1.0
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
        if cached and cached.get("rate"): return cached["rate"]
        return CURRENCY_RATES.get(to_curr, 1.0)

_CURRENCY_ALIASES = {
    "THB": "THB", "JPY": "JPY", "USD": "USD", "KRW": "KRW",
    "บาท": "THB", "baht": "THB",
    "เยน": "JPY", "yen": "JPY",
    "วอน": "KRW", "won": "KRW",
    "ดอลลาร์": "USD", "ดอลลาร์สหรัฐ": "USD", "usd": "USD",
}

def _normalize_currency(token):
    if not token: return None
    t = token.strip()
    if not t: return None
    return _CURRENCY_ALIASES.get(t.upper(), _CURRENCY_ALIASES.get(t.lower(), t.upper() if re.fullmatch(r"[A-Za-z]{3}", t) else None))

def _parse_amount_token(token):
    if not token: return None
    t = token.strip().replace(",", "")
    if not re.fullmatch(r"\d+(?:\.\d{0,2})?", t): return None
    try:
        v = float(t)
        return v if v > 0 else None
    except: return None

def parse_enhanced_expense(text, default_payer_name=None):
    raw = (text or "").strip()
    if not raw: return None, None, None, None, None, None
    parts = [p for p in raw.split() if p.strip()]
    if len(parts) < 2: return None, None, None, None, None, None

    amt_idx = None
    amt_val = None
    for i, p in enumerate(parts):
        if re.fullmatch(r"\d+(?:\.\d{0,2})?", p.replace(",", "")):
            amt_idx, amt_val = i, float(p.replace(",", ""))
            break
        
    if amt_idx is None: return None, None, None, None, None, None

    currency = "THB"
    after_amt_idx = amt_idx + 1
    if after_amt_idx < len(parts):
        maybe_curr = _normalize_currency(parts[after_amt_idx])
        if maybe_curr:
            currency = maybe_curr
            after_amt_idx += 1

    tag = None
    if after_amt_idx < len(parts) and parts[after_amt_idx].startswith("#"):
        tag = parts[after_amt_idx].strip()
        after_amt_idx += 1

    participants = [p.strip() for p in parts[after_amt_idx:] if p.strip()]

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

    if not payer: return None, None, None, None, None, None
    if not item: item = "ค่าใช้จ่าย"

    return payer, item, amt_val, currency, tag, participants

def extract_amount(text):
    if not text: return None
    lines = text.split('\n')
    amount_labels = ['จำนวน', 'amount', 'ยอด', 'total']
    money_pattern = r'(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)'

    # 1) หา amount จากบรรทัด label และบรรทัดถัดไป 1-2 บรรทัดก่อน
    for i, line in enumerate(lines):
        line_lower = line.lower().strip()
        if any(label in line_lower for label in amount_labels):
            window_lines = [line.strip()]
            if i + 1 < len(lines): window_lines.append(lines[i + 1].strip())
            if i + 2 < len(lines): window_lines.append(lines[i + 2].strip())
            for candidate_line in window_lines:
                amounts = re.findall(money_pattern, candidate_line)
                for a in amounts:
                    try:
                        num = float(a.replace(',', ''))
                        if 1 <= num <= 1000000:
                            return num
                    except:
                        continue

    # 2) หาเลขที่อยู่หน้า "บาท" แล้วเลือกค่าบวกที่มากสุดก่อน 0.00
    baht_matches = re.findall(money_pattern + r'\s*บาท', text)
    baht_values = []
    for a in baht_matches:
        try:
            num = float(a.replace(',', ''))
            if 1 <= num <= 1000000:
                baht_values.append(num)
        except:
            continue
    if baht_values:
        return max(baht_values)

    # 3) fallback: หาเลขทศนิยมที่ดูเป็นจำนวนเงิน และตัดเลขวันที่/เวลา/เลขอ้างอิงยาวๆ ออก
    decimal_matches = re.findall(r'(?<![\d/:-])(\d{1,3}(?:,\d{3})*(?:\.\d{2}))(?![\d])', text)
    decimal_values = []
    for a in decimal_matches:
        try:
            num = float(a.replace(',', ''))
            if 1 <= num <= 1000000:
                decimal_values.append(num)
        except:
            continue
    if decimal_values:
        return max(decimal_values)

    return None

def _to_thai_date_str(iso_str):
    """แปลง ISO string (UTC) → วันที่ไทย (UTC+7) format YYYY-MM-DD"""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        dt_thai = dt + timedelta(hours=7)
        return dt_thai.strftime("%Y-%m-%d")
    except:
        return iso_str[:10]  # fallback

def extract_showtime(text):
    if not text: return []
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    showtime_list = []
    # แก้ไข: ลบช่องว่างใน {1,2} และย้าย - ไปท้ายสุดใน character class
    time_pattern = r'(\d{1,2}[:.]\d{2})\s*[-โ€“]\s*(\d{1,2}[:.]\d{2})'
    for i, line in enumerate(lines):
        time_match = re.search(time_pattern, line)
        if not time_match: continue
        time_str = f"{time_match.group(1)}-{time_match.group(2)}".replace('.', ':')
        artist = None
        line_without_time = re.sub(time_pattern, '', line).strip()
        line_without_time = re.sub(r'^[^\wก-๙A-Za-z0-9]+\s*', '', line_without_time).strip()
        if line_without_time and len(line_without_time) > 1: artist = line_without_time
        if not artist:
            for j in range(i - 1, max(i - 3, -1), -1):
                prev_line = lines[j].strip()
                if re.search(time_pattern, prev_line) or re.match(r'^[\d\s:.โ€“\-]+$', prev_line): continue
                prev_clean = re.sub(r'^[^\wก-๙A-Za-z0-9]+\s*', '', prev_line).strip()
                if prev_clean and len(prev_clean) > 1: artist = prev_clean; break
        if not artist:
            for j in range(i + 1, min(i + 3, len(lines))):
                next_line = lines[j].strip()
                if re.search(time_pattern, next_line) or re.match(r'^[\d\s:.โ€“\-]+$', next_line): continue
                next_clean = re.sub(r'^[^\wก-๙A-Za-z0-9]+\s*', '', next_line).strip()
                if next_clean and len(next_clean) > 1: artist = next_clean; break
        if not artist: artist = "Unknown"
        showtime_list.append({"time": time_str, "artist": artist})
    return showtime_list

# =================================================================
# Core DB Functions
# =================================================================
def get_active_trip(user_id, group_id=None):
    if not supabase: return None
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
    if not supabase: return []
    try:
        res = supabase.table("expenses").select("*").eq("trip_id", trip_id).order("id", desc=False).execute()
        return res.data if res.data else []
    except: return []

def update_expense_amount(expense_id, new_amount):
    if not supabase: return False
    try:
        supabase.table("expenses").update({"amount": new_amount}).eq("id", expense_id).execute()
        return True
    except: return False

def compute_trip_balances_thb(trip_id, group_id=None):
    expenses = get_all_expenses(trip_id)
    total_thb = 0.0
    paid_totals = {}
    share_totals = {}
    people = set()
    for exp in expenses:
        curr = exp.get("currency", "THB")
        amt_thb = float(exp.get("amount") or 0)
        if curr != "THB": amt_thb *= (get_exchange_rate(curr, "THB") or 1.0)
        if amt_thb <= 0: continue
        total_thb += amt_thb
        payer = exp.get("payer_name") or get_display_name(exp.get("line_user_id"), group_id)
        if payer:
            paid_totals[payer] = paid_totals.get(payer, 0.0) + amt_thb
            people.add(payer)
        ppl = exp.get("participants") or []
        if isinstance(ppl, str): ppl = [p.strip() for p in ppl.split() if p.strip()]
        if not ppl: ppl = [payer] if payer else []
        share = amt_thb / max(len(ppl), 1) if ppl else 0.0
        for p in ppl:
            if not p: continue
            share_totals[p] = share_totals.get(p, 0.0) + share
            people.add(p)
    return total_thb, paid_totals, share_totals, people

def load_schedules():
    if not supabase: return []
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
            ppl = exp.get('participants') or []
            if isinstance(ppl, str): ppl = [p.strip() for p in ppl.split() if p.strip()]
            data.append({
                "ชื่อทริป": trip_title, "วันที่": date_str, "เวลา": time_str,
                "ชื่อผู้จ่าย": user_name, "รายการ": exp.get('item_name', ''),
                "จำนวนเงิน": exp.get('amount', 0), "สกุล": exp.get('currency', 'THB'),
                "หมวดหมู่": exp.get('tag', ''), "หาร": " ".join(ppl),
            })
        df = pd.DataFrame(data)
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer: df.to_excel(writer, index=False, sheet_name='Expenses')
        output.seek(0)
        return output, None
    except Exception as e: return None, str(e)

def upload_excel_to_supabase(file_buffer, filename):
    if not supabase: return None, "Supabase client not initialized"
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
        patterns = [
            r'(\d{1,2})\s+(มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|กรกฎาคม|สิงหาคม|กันยายน|ตุลาคม|พฤศจิกายน|ธันวาคม)\s+(\d{3,4})',
            r'(\d{1,2})[-/](\d{1,2})[-/](\d{4})\s+(\d{1,2}):(\d{2})'
        ]
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
    if not supabase: return jsonify({"ok": False, "error": "Supabase not initialized"}), 500
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
        except Exception as e: return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/schedules/<schedule_id>", methods=["DELETE"])
def delete_schedule(schedule_id):
    if not supabase: return jsonify({"ok": False, "error": "Supabase not initialized"}), 500
    try:
        supabase.table("schedules").delete().eq("id", schedule_id).execute()
        return jsonify({"ok": True})
    except Exception as e: return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/server-time", methods=["GET"])
def get_server_time():
    import time
    return jsonify({"ok": True, "serverTime": int(time.time() * 1000)})

@app.route("/api/check-showtime", methods=["POST"])
def check_showtime_cron():
    auth = request.headers.get("Authorization", "")
    secret = os.getenv("CRON_SECRET", "")
    if secret and auth != f"Bearer {secret}": return jsonify({"ok": False, "error": "Unauthorized"}), 401
    
    now = datetime.now() + timedelta(hours=7)
    ended = 0; alerted = 0

    def _start_hhmm(t):
        if not t: return None
        s = str(t).split("-")[0].strip().replace(".", ":").strip()
        if re.fullmatch(r"\d{1,2}:\d{2}", s):
            h, m = s.split(":")
            return f"{int(h):02d}:{int(m):02d}"
        return None

    for uid, st in list(user_state.items()):
        if st.get("action") != "showtime_mode": continue
        event_name = st.get("event_name")
        if not event_name: continue
        showtime = load_showtime(event_name)
        schedule = showtime.get("schedule", []) or []
        end_date = st.get("end_date")
        if end_date:
            try:
                end_dt = datetime.strptime(end_date, "%Y-%m-%d")
                if now.date() > end_dt.date():
                    target = st.get("target_id") or uid
                    clear_state(uid)
                    try: line_bot_api.push_message(target, TextSendMessage(text="🛑 Auto-End: Showtime หมดวันที่ตั้งไว้แล้ว"))
                    except Exception as e: logger.error(f"Auto-end push error: {e}")
                    ended += 1; continue
            except: pass
        current_hhmm = now.strftime("%H:%M")
        for item in schedule:
            start = _start_hhmm(item.get("time"))
            if not start or start != current_hhmm: continue
            key = f"{now.strftime('%Y-%m-%d')}|{start}|{item.get('artist','')}"
            if st.get("last_alert_key") == key: continue
            target = st.get("target_id") or uid
            try:
                artist = item.get("artist", "-")
                time_range = item.get("time", start)
                line_bot_api.push_message(target, TextSendMessage(text=f"🎤 Showtime Now: {artist}\n⏱️ {time_range}\n🎪 {event_name}"))
                st["last_alert_key"] = key; alerted += 1
            except Exception as e: logger.error(f"Showtime alert push error: {e}")
            break
    return jsonify({"ok": True, "ended": ended, "alerted": alerted, "serverTime": now.isoformat()})

@app.route("/api/daily-summary", methods=["POST"])
def daily_summary_cron():
    if not supabase: return jsonify({"ok": False, "error": "Supabase not initialized"}), 500
    auth = request.headers.get("Authorization", "")
    secret = os.getenv("CRON_SECRET", "")
    if secret and auth != f"Bearer {secret}": return jsonify({"ok": False, "error": "Unauthorized"}), 401

    try:
        today_str = (datetime.now() + timedelta(hours=7)).strftime("%Y-%m-%d")
        trips_res = supabase.table("trips").select("*").eq("status", "active").execute()
        trips = trips_res.data if trips_res.data else []
        sent_count = 0

        for trip in trips:
            expenses = get_all_expenses(trip['id'])
            today_exp = [e for e in expenses if _to_thai_date_str(e.get('created_at', '')) == today_str]
            if not today_exp: continue

            total_thb = 0.0; currency_totals = {}; categories = {}

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
                if tag not in categories: categories[tag] = {'total_thb': 0.0, 'participants': set()}
                categories[tag]['total_thb'] += amt_thb

                ppl = exp.get('participants') or []
                if isinstance(ppl, str): ppl = [p.strip() for p in ppl.split() if p.strip()]
                if not ppl:
                    payer = exp.get('payer_name') or get_display_name(exp['line_user_id'], trip.get('line_group_id'))
                    ppl = [payer]
                for p in ppl:
                    if p: categories[tag]['participants'].add(str(p))

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

    if text_lower == "state":
        line_bot_api.reply_message(reply_token, TextSendMessage(text=build_state_text(state)))
        return

    if text in ["ทริป"] or text_lower == "trip":
        set_state(user_id, {"action": "wait_trip_name"})
        line_bot_api.reply_message(reply_token, TextSendMessage(text="กรุณาระบุชื่อทริป"))
        return

    if text in ["menu showtime", "showtime menu"]:
        if state and state.get("action") in SHOWTIME_ACTIONS:
            line_bot_api.reply_message(reply_token, build_showtime_menu_flex(state.get("show_date")))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ใช้ 'menu showtime' ได้เฉพาะตอนอยู่ในโหมด Showtime"))
        return

    # --- Follow-up states for Slip ---
    if state and state.get("action") == "wait_slip_payer":
        raw_text = text.strip()
        if not raw_text:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาพิมพ์ชื่ออย่างน้อย 1 คน"))
            return
        if raw_text.lower() in ["ฉัน", "ฉันเอง", "me"]:
            payer_name = get_display_name(user_id, state.get('group_id'))
            participants = [payer_name]
        else:
            participants = [n.strip() for n in raw_text.split() if n.strip()]
            if not participants:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาพิมพ์ชื่ออย่างน้อย 1 คน"))
                return
            payer_name = raw_text
        threading.Thread(
            target=process_slip_with_payer,
            args=(state["message_id"], state["trip_id"], user_id, state.get("group_id"), reply_token, payer_name, participants)
        ).start()
        return

    # --- Follow-up states for Expense ---
    if state and state.get("action") == "wait_expense_participants":
        names = [n.strip() for n in text.split() if n.strip()]
        if not names:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาพิมพ์รายชื่ออย่างน้อย 1 คน"))
            return
        my_name = get_display_name(user_id, state.get("group_id"))
        names = [my_name if n in ["ฉัน", "ฉันเอง", "me"] else n for n in names]
        if not supabase: return
        try:
            supabase.table("expenses").insert({"trip_id": state["trip_id"], "line_user_id": user_id, "payer_name": state["payer_name"], "amount": state["amount"], "item_name": state["item"], "currency": state["currency"], "tag": state["tag"], "participants": names, "slip_url": None}).execute()
            curr_txt = f" {state['currency']}" if state['currency'] != "THB" else ""
            tag_txt = f" ({state['tag']})" if state['tag'] else ""
            ppl_txt = " ".join(names)
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ บันทึก {state['amount']:,.2f}{curr_txt}{tag_txt}\nจ่าย: {state['payer_name']}\nหาร: {ppl_txt}"))
        except Exception as e: logger.error(f"Save expense error: {e}")
        clear_state(user_id)
        return

    if state and state.get("action") == "wait_trip_name":
        if text in ["เมนู", "menu", "help"]:
            line_bot_api.reply_message(reply_token, build_main_menu_flex())
            return
        if text in ["ยกเลิก", "cancel", "exit", "ออก"]:
            clear_state(user_id)
            line_bot_api.reply_message(reply_token, TextSendMessage(text="✅ ยกเลิกการสร้างทริปแล้ว"))
            return
        trip_name = text.strip()
        if not trip_name:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาระบุชื่อทริป"))
            return
        if not supabase: return
        try:
            supabase.table("trips").update({"status": "closed"}).eq("creator_id", user_id).execute()
            supabase.table("trips").insert({"title": trip_name, "status": "active", "line_group_id": group_id, "creator_id": user_id}).execute()
            clear_state(user_id)
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"🚀 เริ่มทริปใหม่: {trip_name} เรียบร้อย!"))
        except Exception as e:
            logger.error(f"Create trip error: {e}")
            line_bot_api.reply_message(reply_token, TextSendMessage(text="สร้างทริปไม่สำเร็จ"))
        return

    # --- Showtime Mode Handling ---
    if state and state.get("action") == "showtime_mode":
        if text_lower == "save":
            if state.get("showtime_temp"):
                event_name = state.get("event_name")
                existing = load_showtime(event_name)
                existing["show_date"] = state.get("show_date")
                existing["schedule"] = sort_showtime_by_time(state["showtime_temp"])
                existing["last_updated"] = datetime.now().isoformat()
                ok = save_showtime(existing, event_name)
                if not ok:
                    line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ บันทึก Showtime ไม่สำเร็จ"))
                    return
            clear_state(user_id)
            line_bot_api.reply_message(reply_token, TextSendMessage(text="Showtime saved."))
            return

        if text_lower in ["end showtime", "stop showtime"]:
            events = list_showtime_events()
            if not events:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่มี event ในระบบให้ลบ"))
                return
            msg = "🗑️ เลือก event ที่ต้องการลบ:\n"
            for i, ev in enumerate(events, 1):
                msg += f"{i}. 🎪 {ev['event_name']} ({ev['count']} วง)\n"
            msg += "\nพิมพ์ 'end showtime [เลข]' เช่น end showtime 1\nหรือ 'exit' เพื่อออกจากโหมด"
            set_state(user_id, {"action": "wait_end_showtime_event_index", "events": events, "target_id": group_id or user_id, "group_id": group_id})
            line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
            return

        # --- menu/help/exit ต้อง bypass ทุก state ใน showtime_mode (รวม edit_mode) ---
        if text in ["เมนู", "menu", "help"]:
            line_bot_api.reply_message(reply_token, TextSendMessage(text=build_showtime_command_text()))
            return

        if text_lower in ["exit", "ออก"]:
            clear_state(user_id)
            line_bot_api.reply_message(reply_token, TextSendMessage(text="✅ ออกจากโหมด Showtime เรียบร้อย\n📸 กลับมารับสลิปปกติแล้วครับ"))
            return

        if state.get("edit_mode"):
            lines = text.splitlines()
            updated = False
            event_name = state.get("event_name")
            existing = load_showtime(event_name)
            schedule = existing.get("schedule", [])
            time_pattern_input = r'^(\d{1,2}[:.]\d{2})\s*[-โ€“]\s*(\d{1,2}[:.]\d{2})\s+(.+)$'
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
                existing["show_date"] = state.get("show_date")
                existing["last_updated"] = datetime.now().isoformat()
                save_showtime(existing, event_name)
                line_bot_api.reply_message(reply_token, TextSendMessage(text="✅ อัปเดต Showtime เสร็จ!\n\n" + format_showtime_message(state.get("show_date"), event_name) + "\n\nพิมพ์ 'save' เพื่อจบ"))
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่พบรูปแบบที่ถูกต้อง\nตัวอย่าง: 13:00-13:50 ROMANCE"))
            return

        # รับ Showtime ผ่านข้อความ (รองรับหลายบรรทัด)
        time_pattern_input = r'^(\d{1,2}[:.]\d{2})\s*[-โ€“]\s*(\d{1,2}[:.]\d{2})\s+(.+)$'
        parsed_rows = []
        for ln in text.splitlines():
            line = ln.strip()
            if not line:
                continue
            mline = re.match(time_pattern_input, line)
            if not mline:
                parsed_rows = []
                break
            parsed_rows.append({
                "time": f"{mline.group(1)}-{mline.group(2)}".replace('.', ':'),
                "artist": mline.group(3).strip()
            })
        if parsed_rows:
            if "showtime_temp" not in state:
                state["showtime_temp"] = []
            for row in parsed_rows:
                found = False
                for item in state["showtime_temp"]:
                    if item["time"] == row["time"]:
                        item["artist"] = row["artist"]
                        found = True
                        break
                if not found:
                    state["showtime_temp"].append(row)
            state["showtime_temp"] = sort_showtime_by_time(state["showtime_temp"])
            set_state(user_id, state)
            date_header = f"📅 วันที่จัดแสดง: {state.get('show_date')}\n" if state.get("show_date") else ""
            msg = f"{date_header}✅ เพิ่ม/อัปเดต Showtime {len(parsed_rows)} รายการ\n\n📋 ตารางชั่วคราว:\n"
            for item in state["showtime_temp"]:
                msg += f"⏱️ {item['time']} | 🎤 {item['artist']}\n"
            msg += "\n👉 ส่งเพิ่มอีก หรือพิมพ์ 'save'"
            line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
            return

        if text_lower == "showtime":
            line_bot_api.reply_message(reply_token, TextSendMessage(text=format_showtime_message(state.get("show_date"), state.get("event_name"))))
            return
        if text_lower in ["edit showtime", "update showtime", "editshowtime"]:
            set_state(user_id, {**state, "edit_mode": True})
            line_bot_api.reply_message(reply_token, TextSendMessage(text="✏️ แก้ไข Showtime\n" + format_showtime_message(state.get("show_date"), state.get("event_name"))))
            return

        allowed = ["save", "showtime", "เพิ่ม", "add showtime", "edit showtime", "update showtime", "เมนู", "menu", "help", "ยกเลิก", "cancel", "end showtime", "exit", "ออก"]
        if re.match(r'^end showtime\s+\d+$', text_lower): allowed.append(text_lower)
        if text_lower not in allowed and text not in allowed:
            if not re.match(time_pattern_input, text):
                line_bot_api.reply_message(reply_token, TextSendMessage(text="🔒 ตอนนี้อยู่ในโหมด Showtime\nพิมพ์ 'menu' เพื่อดูคำสั่ง หรือพิมพ์ 'menu showtime' เพื่อเปิดเมนู"))
            return

    if state and state.get("action") == "wait_end_showtime_event_index":
        if text_lower in ["exit", "ออก"]:
            clear_state(user_id)
            line_bot_api.reply_message(reply_token, TextSendMessage(text="✅ ออกจากโหมดลบ event แล้ว"))
            return
        m = re.match(r"^end\s+showtime\s+(\d+)$", text_lower)
        if not m:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="พิมพ์ 'end showtime [เลข]' หรือ 'exit'"))
            return
        events = state.get("events", [])
        idx = int(m.group(1))
        if idx < 1 or idx > len(events):
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"⚠️ เลขไม่ถูกต้อง (1-{len(events)})"))
            return
        selected = events[idx - 1]
        set_state(user_id, {"action": "wait_end_showtime_confirm", "event_name": selected["event_name"]})
        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"ยืนยันลบ event '{selected['event_name']}' ทั้งหมด?\nพิมพ์ 'yes' เพื่อยืนยัน หรือ 'exit' เพื่อยกเลิก"))
        return

    if state and state.get("action") == "wait_end_showtime_confirm":
        if text_lower in ["yes", "y"]:
            event_name = state.get("event_name")
            ok = delete_showtime_event(event_name)
            clear_state(user_id)
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ ลบ event '{event_name}' เรียบร้อย" if ok else "❌ ลบ event ไม่สำเร็จ"))
            return
        if text_lower in ["exit", "ออก"]:
            clear_state(user_id)
            line_bot_api.reply_message(reply_token, TextSendMessage(text="✅ ยกเลิกการลบ event แล้ว"))
            return
        line_bot_api.reply_message(reply_token, TextSendMessage(text="พิมพ์ 'yes' เพื่อยืนยัน หรือ 'exit' เพื่อยกเลิก"))
        return

    # --- Entry: Start Showtime ---
    if text_lower == "add showtime":
        set_state(user_id, {"action": "wait_showtime_event_name"})
        line_bot_api.reply_message(reply_token, TextSendMessage(text="กรุณาระบุชื่อ Showtime/Event\nเช่น Big Mountain 2026"))
        return

    if text_lower == "showtime":
        events = list_showtime_events()
        if events:
            msg = build_showtime_event_list_message()
            msg += "\n\nพิมพ์ 'เพิ่ม' เพื่อเพิ่มตารางใหม่\nหรือพิมพ์ 'showtime [เลข]' เพื่อดูตารางเดิม"
            set_state(user_id, {"action": "wait_showtime_add_confirm", "events": events})
            line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
            return
        set_state(user_id, {"action": "wait_showtime_event_name"})
        line_bot_api.reply_message(reply_token, TextSendMessage(text="กรุณาระบุชื่อ Showtime/Event\nเช่น Big Mountain 2026"))
        return

    if state and state.get("action") == "wait_showtime_add_confirm":
        if text in ["เมนู", "menu", "help"]:
            line_bot_api.reply_message(reply_token, TextSendMessage(text=build_showtime_command_text()))
            return
        if text_lower in ["ยกเลิก", "cancel", "exit", "ออก"]:
            clear_state(user_id)
            line_bot_api.reply_message(reply_token, TextSendMessage(text="✅ ยกเลิกโหมด Showtime แล้ว"))
            return
        m_end_direct = re.match(r"^end\s+showtime\s+(\d+)$", text_lower)
        if m_end_direct:
            events = state.get("events", []) or list_showtime_events()
            idx = int(m_end_direct.group(1))
            if idx < 1 or idx > len(events):
                line_bot_api.reply_message(reply_token, TextSendMessage(text=f"⚠️ เลขไม่ถูกต้อง (1-{len(events)})"))
                return
            selected = events[idx - 1]
            set_state(user_id, {"action": "wait_end_showtime_confirm", "event_name": selected["event_name"]})
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"ยืนยันลบ event '{selected['event_name']}' ทั้งหมด?\nพิมพ์ 'yes' เพื่อยืนยัน หรือ 'exit' เพื่อยกเลิก"))
            return
        if text_lower in ["end showtime", "stop showtime"]:
            events = list_showtime_events()
            if not events:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่มี event ในระบบให้ลบ"))
                return
            msg = "🗑️ เลือก event ที่ต้องการลบ:\n"
            for i, ev in enumerate(events, 1):
                msg += f"{i}. 🎪 {ev['event_name']} ({ev['count']} วง)\n"
            msg += "\nพิมพ์ 'end showtime [เลข]' เช่น end showtime 1\nหรือ 'exit' เพื่อออกจากโหมด"
            set_state(user_id, {"action": "wait_end_showtime_event_index", "events": events, "target_id": group_id or user_id, "group_id": group_id})
            line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
            return
        events = state.get("events", [])
        if text_lower in ["เพิ่ม", "add showtime", "yes", "y"]:
            set_state(user_id, {"action": "wait_showtime_event_name"})
            line_bot_api.reply_message(reply_token, TextSendMessage(text="กรุณาระบุชื่อ Showtime/Event"))
            return
        m = re.match(r"^showtime\s+(\d+)$", text_lower)
        if m:
            idx = int(m.group(1))
            if idx < 1 or idx > len(events):
                line_bot_api.reply_message(reply_token, TextSendMessage(text=f"เลขไม่ถูกต้อง (1-{len(events)})"))
                return
            selected = events[idx - 1]
            msg = format_showtime_message(None, selected["event_name"])
            msg += "\n\nพิมพ์ 'เพิ่ม' ถ้าต้องการเพิ่มตารางใหม่"
            line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
            return
        line_bot_api.reply_message(reply_token, TextSendMessage(text="พิมพ์ 'เพิ่ม' / 'add showtime' / 'showtime [เลข]' / 'end showtime'"))
        return

    if state and state.get("action") == "wait_showtime_event_name":
        if text in ["เมนู", "menu", "help"]:
            line_bot_api.reply_message(reply_token, TextSendMessage(text=build_showtime_command_text()))
            return
        if text_lower in ["ยกเลิก", "cancel", "exit", "ออก"]:
            clear_state(user_id)
            line_bot_api.reply_message(reply_token, TextSendMessage(text="✅ ยกเลิกโหมด Showtime แล้ว"))
            return
        event_name = text.strip()
        if not event_name:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ชื่อ event ห้ามว่าง"))
            return
        set_state(user_id, {"action": "wait_showtime_date", "event_name": event_name})
        line_bot_api.reply_message(reply_token, TextSendMessage(text="📅 กรุณาระบุวันที่จัดแสดง (YYYY-MM-DD)\nเช่น 2026-05-30\n(พิมพ์ 'ข้าม' หากไม่ต้องการกำหนด)"))
        return

    if state and state.get("action") == "wait_showtime_date":
        if text in ["เมนู", "menu", "help"]:
            line_bot_api.reply_message(reply_token, TextSendMessage(text=build_showtime_command_text()))
            return
        if text_lower in ["ยกเลิก", "cancel", "exit", "ออก"]:
            clear_state(user_id)
            line_bot_api.reply_message(reply_token, TextSendMessage(text="✅ ยกเลิกโหมด Showtime แล้ว"))
            return
        show_date = None
        if text_lower != "ข้าม" and text_lower != "skip":
            try:
                parsed = datetime.strptime(text.strip(), "%Y-%m-%d")
                show_date = parsed.strftime("%Y-%m-%d")
            except ValueError:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ รูปแบบวันที่ไม่ถูกต้อง ใช้ YYYY-MM-DD หรือพิมพ์ 'ข้าม'"))
                return
        event_name = state.get("event_name")
        set_state(user_id, {"action": "showtime_mode", "event_name": event_name, "show_date": show_date, "end_date": show_date, "edit_mode": False, "target_id": group_id or user_id, "group_id": group_id, "last_alert_key": None})
        msg = format_showtime_message(show_date, event_name) + "\n\nส่งรูปหรือพิมพ์ตารางแสดง แล้วพิมพ์ 'save' เพื่อบันทึก"
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return

    # --- Normal Commands ---
    if text in ["เมนู", "menu", "help"]:
        line_bot_api.reply_message(reply_token, build_main_menu_flex())
        return

    if text_lower in ["exit", "ออก"]:
        line_bot_api.reply_message(reply_token, TextSendMessage(text="📋 ตอนนี้อยู่ในโหมดปกติ\nไม่ได้อยู่ในโหมด Showtime"))
        return

    if text in ["ยกเลิก", "cancel"]:
        if state:
            clear_state(user_id)
            line_bot_api.reply_message(reply_token, TextSendMessage(text="✅ ยกเลิกเรียบร้อย"))
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
                        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ แก้ไขรายการ ID {inline_id:04d} เป็น {inline_amount:,.2f} บาท เรียบร้อย!"))
                    else: line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ แก้ไขไม่สำเร็จ"))
                return
            except: pass
        expenses = get_all_expenses(trip['id'])
        if not expenses:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่มีรายการค่าใช้จ่ายให้แก้ไข"))
            return
        lines_edit = [f"ID {exp['id']:04d} | {exp['item_name'][:30]} | {exp['amount']:,.2f} บาท" for exp in expenses]
        line_bot_api.reply_message(reply_token, build_report_flex(title="✏️ เลือกรายการแก้ไข", subtitle="พิมพ์: edit [ID] [ยอดใหม่]", lines=lines_edit, alt_text="แก้ไขรายการ"))
        return

    trip_match = re.match(r"^(ทริป|trip)\s+(.+)$", text, re.IGNORECASE)
    if trip_match:
        trip_name = trip_match.group(2).strip()
        if not trip_name:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาระบุชื่อทริป"))
            return
        if not supabase: return
        try:
            supabase.table("trips").update({"status": "closed"}).eq("creator_id", user_id).execute()
            supabase.table("trips").insert({"title": trip_name, "status": "active", "line_group_id": group_id, "creator_id": user_id}).execute()
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"🚀 เริ่มทริปใหม่: {trip_name} เรียบร้อย!"))
        except Exception as e:
            logger.error(f"Create trip error: {e}")
            line_bot_api.reply_message(reply_token, TextSendMessage(text=" สร้างทริปไม่สำเร็จ"))
        return

    if text == "id":
        msg = f" User ID: {user_id}"
        if group_id: msg += f"\n๐‘ฅ Group ID: {group_id}"
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return

    if text == "ยอด" or text_lower == "sum":
        trip = get_active_trip(user_id, group_id)
        if not trip:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่มีทริปที่กำลังทำงานอยู่"))
            return
        expenses = get_all_expenses(trip['id'])
        if not expenses:
            line_bot_api.reply_message(reply_token, TextSendMessage(text=" ยังไม่มีรายการค่าใช้จ่าย"))
            return
        total_thb = 0
        categories = {}
        for exp in expenses:
            amt_thb = exp['amount']
            curr = exp.get('currency', 'THB')
            if curr != 'THB': amt_thb *= (get_exchange_rate(curr, 'THB') or 1.0)
            total_thb += amt_thb
            tag = exp.get('tag') or '#ทั่วไป'
            if tag not in categories: categories[tag] = {'total': 0, 'participants': set()}
            categories[tag]['total'] += amt_thb
            ppl = exp.get('participants') or []
            if isinstance(ppl, str): ppl = [p.strip() for p in ppl.split() if p.strip()]
            if not ppl:
                payer = exp.get('payer_name') or get_display_name(exp['line_user_id'], group_id)
                ppl = [payer]
            for p in ppl:
                if p: categories[tag]['participants'].add(str(p))
        lines = [f"ยอดรวม {total_thb:,.2f} บาท"]
        for tag, data in sorted(categories.items()):
            ppl_str = " ".join(sorted(data['participants']))
            lines.append(f"{tag} {data['total']:,.0f} {ppl_str}")
        line_bot_api.reply_message(reply_token, build_report_flex(title="💰 ยอดรวมทริป", subtitle=None, lines=lines, alt_text="ยอดรวม"))
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
        today_exp = [e for e in expenses if _to_thai_date_str(e.get('created_at', '')) == today_str]
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
            if curr != 'THB': amt_thb *= (get_exchange_rate(curr, 'THB') or 1.0)
            total_thb += amt_thb
            tag = exp.get('tag') or '#ทั่วไป'
            if tag not in categories: categories[tag] = {'total_thb': 0, 'participants': set()}
            categories[tag]['total_thb'] += amt_thb
            ppl = exp.get('participants') or []
            if isinstance(ppl, str): ppl = [p.strip() for p in ppl.split() if p.strip()]
            if not ppl:
                payer = exp.get('payer_name') or get_display_name(exp['line_user_id'], group_id)
                ppl = [payer]
            for p in ppl:
                if p: categories[tag]['participants'].add(str(p))
        rate_to_target = get_exchange_rate("THB", target_curr) if target_curr != "THB" else 1.0
        total_target = total_thb * rate_to_target
        subtitle_parts = []
        if currency_totals:
            cur_lines = [f"{c} {float(v):,.2f}" for c, v in sorted(currency_totals.items())]
            subtitle_parts.append("💱 ยอดตามสกุล: " + " | ".join(cur_lines))
        if target_curr != "THB":
            subtitle_parts.append(f"1 THB = {rate_to_target:.4f} {target_curr}")
            subtitle_parts.append(f" รวมวันนี้: {total_thb:,.2f} บาท (≈ {total_target:,.2f} {target_curr})")
        else:
            subtitle_parts.append(f"💵 รวมวันนี้: {total_thb:,.2f} บาท")
        lines = []
        for tag, data in sorted(categories.items()):
            ppl_str = " ".join(sorted(data['participants']))
            line = f"{tag} {data['total_thb']:,.0f} {ppl_str}"
            if target_curr != "THB": line += f" (โ {data['total_thb'] * rate_to_target:,.2f} {target_curr})"
            lines.append(line)
        line_bot_api.reply_message(reply_token, build_report_flex(title=f"📅 ยอดวันนี้ ({today_str})", subtitle="\n".join(subtitle_parts), lines=lines, alt_text="ยอดวันนี้"))
        return

    # --- BLOCK 3: Enhanced Expense Parsing ---
    # guard: ห้าม parse expense ขณะรอ input หรืออยู่ใน state อื่น
    SLIP_STATES = {"wait_slip_payer", "wait_expense_participants",
                   "showtime_mode", "wait_showtime_add_confirm", "wait_showtime_event_name", "wait_showtime_date",
                   "wait_end_showtime_event_index", "wait_end_showtime_confirm", "wait_trip_name", "end_trip",
                   "edit_selection", "edit_amount", "stop_event", "export_history"}
    trip = get_active_trip(user_id, group_id)
    if trip and not (state and state.get("action") in SLIP_STATES):
        default_payer = get_display_name(user_id, group_id)
        payer, item, amount, currency, tag, participants = parse_enhanced_expense(text, default_payer_name=default_payer)
        if amount and amount > 0:
            if not participants:
                set_state(user_id, {"action": "wait_expense_participants", "trip_id": trip['id'], "group_id": group_id, "payer_name": payer, "item": item, "amount": amount, "currency": currency, "tag": tag})
                line_bot_api.reply_message(reply_token, TextSendMessage(text="👥 หารกับใครบ้าง?\nพิมพ์ชื่อคั่นด้วยเว้นวรรค เช่น: บอล ปาค เอ็ม"))
                return
            if not supabase: return
            try:
                supabase.table("expenses").insert({"trip_id": trip['id'], "line_user_id": user_id, "payer_name": payer, "amount": amount, "item_name": item or "ค่าใช้จ่าย", "currency": currency, "tag": tag, "participants": participants, "slip_url": None}).execute()
                curr_txt = f" {currency}" if currency != "THB" else ""
                tag_txt = f" ({tag})" if tag else ""
                ppl_txt = " ".join(participants or [])
                line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ บันทึก {amount:,.2f}{curr_txt}{tag_txt}\nจ่าย: {payer}\nหาร: {ppl_txt}"))
            except Exception as e: logger.error(f"Save expense error: {e}")
        return

    # --- Other Commands (End Trip, Excel, History, Event) ---
    if text.startswith("จบทริป ") or text_lower.startswith("end trip "):
        trip = get_active_trip(user_id, group_id)
        if not trip:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่มีทริปที่กำลังทำงานอยู่"))
            return
        currency_code = "THB"
        if text.startswith("จบทrip "):
            parts = text.split()
            if len(parts) >= 2: currency_code = parts[1].upper()
        elif text_lower.startswith("end trip "):
            parts = text_lower.split()
            if len(parts) >= 3: currency_code = parts[2].upper()
        set_state(user_id, {"action": "end_trip", "trip_id": trip['id'], "trip_title": trip['title'], "currency_code": currency_code})
        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"🏁 ปิดทริป: {trip['title']}\n💱 แปลงเป็นสกุล: {currency_code}\n\n👥 ระบุจำนวนคนที่จะหาร: "))
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
            if rate: exchange_rate = rate
            else: currency_code = "THB"
        msg = f"🚀 ทริป: {trip_title}\n👥 จำนวนคน: {real_n}\n\n"
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
                if diff > 0: msg += f"• {name}: รับคืน {diff:,.2f} บาท (≈ {diff * exchange_rate:,.2f} {currency_code})\n"
                elif diff < 0: msg += f"• {name}: จ่ายเพิ่ม {abs(diff):,.2f} บาท (≈ {abs(diff) * exchange_rate:,.2f} {currency_code})\n"
                else: msg += f"• {name}: เรียบร้อยแล้ว\n"
            else:
                if diff > 0: msg += f"• {name}: รับคืน {diff:,.2f} บาท\n"
                elif diff < 0: msg += f"• {name}: จ่ายเพิ่ม {abs(diff):,.2f} บาท\n"
                else: msg += f"• {name}: เรียบร้อยแล้ว\n"
        try:
            if supabase: supabase.table("trips").update({"status": "closed", "currency_code": currency_code}).eq("id", trip_id).execute()
        except Exception as e: logger.error(f"Close trip error: {e}")
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
        if not supabase: return
        try:
            res = supabase.table("trips").select("*").order("created_at", desc=True).limit(10).execute()
            all_trips = res.data if res.data else []
            if not all_trips:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="📜 ยังไม่มีประวัติทริป"))
                return
            msg = "📜 ประวัติทริป (10 ทริปล่าสุด):\n\n"
            for i, trip in enumerate(all_trips, 1):
                status_icon = "๐ข" if trip['status'] == 'active' else "๐”ด"
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
                        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"โ {error}"))
                        clear_state(user_id)
                        return
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    filename = f"{selected['title']}_{timestamp}.xlsx"
                    public_url, upload_error = upload_excel_to_supabase(excel_buffer, filename)
                    if upload_error:
                        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"โ {upload_error}"))
                        clear_state(user_id)
                        return
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ Excel สำเร็จ!\n📊 {selected['title']}\n🔗 {public_url}"))
                else:
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"⚠️ หมายเลขไม่ถูกต้อง (มี 1-{len(trips)})"))
            except ValueError:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาพิมพ์ตัวเลขเท่านั้น เช่น excel 1"))
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
            msg = f"🔍 ตรวจสอบรายชื่อคิว Event ปัจจุบัน...\n=======================\nℹ️ ไม่มีคิว Event ที่เปิดอยู่\n-----------------------\n\n💻 ลิงก์ควบคุมแผงระบบ:\n{base_url}"
        else:
            msg = "🔍 ตรวจสอบรายชื่อคิว Event ปัจจุบัน...\n=======================\n"
            for i, e in enumerate(events, 1):
                msg += f"{i}. งาน: {e.get('name', '-')}\n⏰ เวลาขาย: {e.get('saleTime', '-')}\n ลิงก์งาน: {e.get('url', '-')}\n-----------------------\n"
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
        msg += " พิมพ์เฉพาะ [ตัวเลขลำดับ] เพื่อระบุเลือกปิดงานชิ้นนั้นได้เลยครับ"
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return

    if state and state.get("action") == "stop_event":
        if not supabase: return
        try:
            choice = int(text_lower) - 1
            events = state["events"]
            if 0 <= choice < len(events):
                selected = events[choice]
                supabase.table("schedules").update({"active": False}).eq("id", selected.get('id')).execute()
                line_bot_api.reply_message(reply_token, TextSendMessage(text=f"✅ สั่งปิดงานเรียบร้อยแล้ว!\n🛑 สั่งหยุดภารกิจงาน: {selected.get('name', '-')}"))
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ หมายเลขไม่ถูกต้อง กรุณาลองใหม่"))
        except ValueError:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาพิมพ์หมายเลขเท่านั้น"))
        except Exception as e:
            logger.error(f"Stop event error: {e}")
            line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ เกิดข้อผิดพลาด กรุณาลองใหม่"))
        clear_state(user_id)
        return

@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    user_id = event.source.user_id
    group_id = getattr(event.source, 'group_id', None)
    reply_token = event.reply_token
    state = get_state(user_id)
    
    if state and state.get("action") == "showtime_mode":
        if not vision_client: return
        try:
            message_content = line_bot_api.get_message_content(event.message.id)
            image_bytes = b''.join(message_content.iter_content())
            response = vision_client.text_detection(image=vision.Image(content=image_bytes))
            text_detected = response.text_annotations[0].description if response.text_annotations else ""
            showtime_list = extract_showtime(text_detected)
            if showtime_list:
                if state.get("edit_mode"):
                    existing = load_showtime(state.get("event_name"))
                    schedule = existing.get("schedule", [])
                    for new_item in showtime_list:
                        found = False
                        for i, old_item in enumerate(schedule):
                            if old_item["time"] == new_item["time"]: schedule[i] = new_item; found = True; break
                        if not found: schedule.append(new_item)
                    existing["schedule"] = sort_showtime_by_time(schedule)
                    existing["show_date"] = state.get("show_date")
                    existing["last_updated"] = datetime.now().isoformat()
                    save_showtime(existing, state.get("event_name"))
                    msg = "✅ อัปเดต Showtime เสร็จ!\n\n" + format_showtime_message(state.get("show_date"), state.get("event_name"))
                else:
                    state["showtime_temp"] = showtime_list
                    msg = "✅ อ่านข้อมูล Showtime สำเร็จ\n\n📋 ตารางชั่วคราว:\n"
                    for item in showtime_list: msg += f"⏱️ {item['time']} | 🎤 {item['artist']}\n"
                    msg += "\n👉 พิมพ์ 'save' เพื่อบันทึก"
                line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่พบข้อมูล Showtime ในรูป"))
        except Exception as e:
            logger.error(f"Showtime OCR error: {e}")
            logger.error(traceback.format_exc())
            line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ ไม่สามารถอ่านรูปได้"))
        return
    if state and state.get("action") in SHOWTIME_ACTIONS:
        line_bot_api.reply_message(
            reply_token,
            TextSendMessage(text="📌 ตอนนี้อยู่ในโหมด Showtime\nพิมพ์ 'menu' เพื่อดูคำสั่ง หรือพิมพ์ 'menu showtime' เพื่อเปิดเมนู"),
        )
        return

    trip = get_active_trip(user_id, group_id)
    if not trip:
        line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่มีทริปที่กำลังทำงานอยู่"))
        return
    set_state(user_id, {"action": "wait_slip_payer", "message_id": event.message.id, "trip_id": trip['id'], "group_id": group_id})
    line_bot_api.reply_message(reply_token, TextSendMessage(text="🧾 พบสลิป/บิล\nกรุณาพิมพ์ชื่อคนที่ต้องรับผิดชอบยอดนี้\nเช่น บอล ปาค\n(หรือพิมพ์ 'ฉัน' เพื่อใช้ชื่อคุณ)"))

def process_slip_with_payer(message_id, trip_id, user_id, group_id, reply_token, payer_name, participants):
    if not vision_client or not supabase: return
    try:
        message_content = line_bot_api.get_message_content(message_id)
        image_bytes = b''.join(message_content.iter_content())
        response = vision_client.text_detection(image=vision.Image(content=image_bytes))
        text_detected = response.text_annotations[0].description if response.text_annotations else ""
        amount = extract_amount(text_detected)
        if amount:
            timestamp = datetime.now().strftime('%d/%m/%y %H:%M:%S')
            result = supabase.table("expenses").insert({"trip_id": trip_id, "line_user_id": user_id, "payer_name": payer_name, "amount": amount, "item_name": f"บิล {timestamp}", "currency": "THB", "tag": "#สลิป", "participants": participants, "slip_url": f"slip_{message_id}"}).execute()
            new_id = result.data[0]['id'] if result.data else None
            ppl_txt = " ".join(participants or [])
            success_msg = f"✅ บันทึก {amount:,.2f} บาท (#สลิป)\nจ่าย: {payer_name}\nหาร: {ppl_txt}"
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


