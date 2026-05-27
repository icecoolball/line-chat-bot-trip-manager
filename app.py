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
        alt_text="เน€เธกเธเธนเธเธณเธชเธฑเนเธ",
        contents=BubbleContainer(
            size='mega',
            body=BoxComponent(
                layout='vertical',
                spacing='md',
                contents=[
                    TextComponent(text='๐“ Trip Manager', weight='bold', size='xl', align='center', color='#1DB446'),
                    TextComponent(text='เน€เธฅเธทเธญเธเธเธณเธชเธฑเนเธเธ—เธตเนเธ•เนเธญเธเธเธฒเธฃเนเธเนเธเธฒเธ', size='sm', color='#999999', align='center', wrap=True)
                ]
            ),
            footer=BoxComponent(
                layout='vertical',
                spacing='sm',
                flex=0,
                contents=[
                    ButtonComponent(style='primary', color='#1DB446', height='md', action=MessageAction(label='๐€ เธชเธฃเนเธฒเธเธ—เธฃเธดเธ', text='เธ—เธฃเธดเธ ')),
                    ButtonComponent(style='secondary', height='md', action=MessageAction(label='๐’ฐ เธขเธญเธ”เธฃเธงเธก', text='เธขเธญเธ”')),
                    ButtonComponent(style='secondary', height='md', action=MessageAction(label='โ๏ธ เนเธเนเนเธ', text='edit')),
                    ButtonComponent(style='secondary', height='md', action=MessageAction(label='๐ค Showtime', text='showtime')),
                    ButtonComponent(style='secondary', height='md', action=MessageAction(label='โ“ เน€เธกเธเธน', text='เน€เธกเธเธน'))
                ]
            )
        )
    )

def build_showtime_menu_flex(show_date=None):
    info_text = f"๐“… เธงเธฑเธเธ—เธตเนเธเธฑเธ”เนเธชเธ”เธ: {show_date}" if show_date else "เนเธกเนเนเธ”เนเธเธณเธซเธเธ”เธงเธฑเธเธเธฑเธ”เนเธชเธ”เธ"
    return FlexSendMessage(
        alt_text="Showtime Menu",
        contents=BubbleContainer(
            size='mega',
            body=BoxComponent(
                layout='vertical',
                spacing='md',
                contents=[
                    TextComponent(text='๐ค Showtime Mode', weight='bold', size='xl', color='#FF5551', align='center'),
                    TextComponent(text=f'เธชเธ–เธฒเธเธฐ: เน€เธเธดเธ”เธญเธขเธนเน\n{info_text}', size='sm', wrap=True, align='center', color='#666666', margin='md')
                ]
            ),
            footer=BoxComponent(
                layout='vertical',
                spacing='sm',
                flex=0,
                contents=[
                    ButtonComponent(style='primary', color='#1DB446', height='md', action=MessageAction(label='๐’พ เธเธฑเธเธ—เธถเธ & เธญเธญเธ', text='save')),
                    ButtonComponent(style='secondary', height='md', action=MessageAction(label='๐‘๏ธ เธ”เธนเธ•เธฒเธฃเธฒเธ', text='showtime')),
                    ButtonComponent(style='secondary', height='md', action=MessageAction(label='โ๏ธ เนเธเนเนเธเธเนเธญเธเธงเธฒเธก', text='editshowtime')),
                    ButtonComponent(style='primary', color='#FF5551', height='md', action=MessageAction(label='๐‘ เธเธ Showtime', text='end showtime')),
                    ButtonComponent(style='secondary', color='#888888', height='md', action=MessageAction(label=' เธญเธญเธ (Exit)', text='exit'))
                ]
            )
        )
    )

def build_report_flex(title, subtitle, lines, alt_text="เธฃเธฒเธขเธเธฒเธ"):
    safe_lines = lines[:12]
    if len(lines) > 12:
        safe_lines.append(f"...เธญเธตเธ {len(lines)-12} เธฃเธฒเธขเธเธฒเธฃ")
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
def load_showtime(event_name=None):
    if not supabase: return {"schedule": [], "last_updated": None}
    try:
        query = supabase.table("showtimes").select("*")
        if event_name:
            query = query.eq("event_name", event_name)
        res = query.execute()
        schedule = res.data if res.data else []
        return {"event_name": event_name, "schedule": schedule, "last_updated": datetime.now().isoformat()}
    except Exception as e:
        logger.error(f"Load showtime error: {e}")
        return {"event_name": event_name, "schedule": [], "last_updated": None}

def save_showtime(showtime_data, event_name):
    if not supabase: return False
    try:
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
        counts = {}
        for row in rows:
            name = (row.get("event_name") or "default").strip() or "default"
            counts[name] = counts.get(name, 0) + 1
        return [{"event_name": k, "count": v} for k, v in sorted(counts.items(), key=lambda x: x[0].lower())]
    except Exception as e:
        logger.error(f"List showtime events error: {e}")
        return []

def delete_showtime_event(event_name):
    if not supabase: return False
    try:
        supabase.table("showtimes").delete().eq("event_name", event_name).execute()
        return True
    except Exception as e:
        logger.error(f"Delete showtime event error: {e}")
        return False

def build_showtime_event_list_message():
    events = list_showtime_events()
    if not events:
        return "เธขเธฑเธเนเธกเนเธกเธตเธ•เธฒเธฃเธฒเธ Showtime เนเธเธฃเธฐเธเธ"
    msg = "เธ•เธฒเธฃเธฒเธ Showtime เธ—เธตเนเธกเธตเธญเธขเธนเน:\n"
    for i, ev in enumerate(events, 1):
        msg += f"{i}. {ev['event_name']} ({ev['count']} เธงเธ)\n"
    return msg

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
    if not showtime.get("schedule"): return "โน๏ธ เธขเธฑเธเนเธกเนเธกเธตเธเนเธญเธกเธนเธฅ Showtime"
    sorted_schedule = sort_showtime_by_time(showtime.get("schedule", []))
    date_header = f"๐“… เธงเธฑเธเธ—เธตเนเธเธฑเธ”เนเธชเธ”เธ: {show_date}\n" if show_date else ""
    msg = f"{date_header}๐“ เธ•เธฒเธฃเธฒเธเธเธฒเธฃเนเธชเธ”เธ:\n\n"
    for item in sorted_schedule:
        time_display = item.get('time', '-').replace('.', ':')
        artist = item.get('artist', '-')
        msg += f"โฑ๏ธ {time_display} | ๐ค {artist}\n"
    return msg

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
    "เธเธฒเธ—": "THB", "baht": "THB",
    "เน€เธขเธ": "JPY", "yen": "JPY",
    "เธงเธญเธ": "KRW", "won": "KRW",
    "เธ”เธญเธฅเธฅเธฒเธฃเน": "USD", "เธ”เธญเธฅเธฅเธฒเธฃเนเธชเธซเธฃเธฑเธ": "USD", "usd": "USD",
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
    if not item: item = "เธเนเธฒเนเธเนเธเนเธฒเธข"

    return payer, item, amt_val, currency, tag, participants

def extract_amount(text):
    if not text: return None
    lines = text.split('\n')
    amount_labels = ['เธเธณเธเธงเธ', 'amount']
    for i, line in enumerate(lines):
        line_lower = line.lower().strip()
        if any(label in line_lower for label in amount_labels):
            amounts = re.findall(r'(\d{1,3}(?:,\d{3})(?:\.\d{1,2})?)', line)
            for a in amounts:
                try:
                    num = float(a.replace(',', ''))
                    if 1 <= num <= 1000000: return num
                except: continue
        if i + 1 < len(lines):
            next_line = lines[i + 1].strip()
            amounts = re.findall(r'(\d{1,3}(?:,\d{3})(?:\.\d{1,2})?)', next_line)
            for a in amounts:
                try:
                    num = float(a.replace(',', ''))
                    if 1 <= num <= 1000000: return num
                except: continue
    baht_matches = re.findall(r'(\d{1,3}(?:,\d{3})(?:\.\d{1,2})?)\sเธเธฒเธ—', text)
    for a in baht_matches:
        try:
            num = float(a.replace(',', ''))
            if 1 <= num <= 1000000: return num
        except: continue
    return None

def _to_thai_date_str(iso_str):
    """เนเธเธฅเธ ISO string (UTC) โ’ เธงเธฑเธเธ—เธตเนเนเธ—เธข (UTC+7) format YYYY-MM-DD"""
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
    # เนเธเนเนเธ: เธฅเธเธเนเธญเธเธงเนเธฒเธเนเธ {1,2} เนเธฅเธฐเธขเนเธฒเธข - เนเธเธ—เนเธฒเธขเธชเธธเธ”เนเธ character class
    time_pattern = r'(\d{1,2}[:.]\d{2})\s*[-โ€“]\s*(\d{1,2}[:.]\d{2})'
    for i, line in enumerate(lines):
        time_match = re.search(time_pattern, line)
        if not time_match: continue
        time_str = f"{time_match.group(1)}-{time_match.group(2)}".replace('.', ':')
        artist = None
        line_without_time = re.sub(time_pattern, '', line).strip()
        line_without_time = re.sub(r'^[โฑ๏ธ๐ค๐“๏ธ๐ต]+\s*', '', line_without_time).strip()
        if line_without_time and len(line_without_time) > 1: artist = line_without_time
        if not artist:
            for j in range(i - 1, max(i - 3, -1), -1):
                prev_line = lines[j].strip()
                if re.search(time_pattern, prev_line) or re.match(r'^[\d\s:.โ€“\-]+$', prev_line): continue
                prev_clean = re.sub(r'^[โฑ๏ธ๐“โ๏ธ๐ต]+\s*', '', prev_line).strip()
                if prev_clean and len(prev_clean) > 1: artist = prev_clean; break
        if not artist:
            for j in range(i + 1, min(i + 3, len(lines))):
                next_line = lines[j].strip()
                if re.search(time_pattern, next_line) or re.match(r'^[\d\s:.โ€“\-]+$', next_line): continue
                next_clean = re.sub(r'^[โฑ๏ธ๐ค๐“โ๏ธ๐ต]+\s*', '', next_line).strip()
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
        if not expenses: return None, "เนเธกเนเธกเธตเธเนเธญเธกเธนเธฅเธเนเธฒเนเธเนเธเนเธฒเธขเนเธเธ—เธฃเธดเธเธเธตเน"
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
                "เธเธทเนเธญเธ—เธฃเธดเธ": trip_title, "เธงเธฑเธเธ—เธตเน": date_str, "เน€เธงเธฅเธฒ": time_str,
                "เธเธทเนเธญเธเธนเนเธเนเธฒเธข": user_name, "เธฃเธฒเธขเธเธฒเธฃ": exp.get('item_name', ''),
                "เธเธณเธเธงเธเน€เธเธดเธ": exp.get('amount', 0), "เธชเธเธธเธฅ": exp.get('currency', 'THB'),
                "เธซเธกเธงเธ”เธซเธกเธนเน": exp.get('tag', ''), "เธซเธฒเธฃ": " ".join(ppl),
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
    if not url: return jsonify({"ok": False, "error": "เนเธกเนเธกเธต URL"}), 400
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        patterns = [
            r'(\d{1,2})\s+(เธกเธเธฃเธฒเธเธก|เธเธธเธกเธ เธฒเธเธฑเธเธเน|เธกเธตเธเธฒเธเธก|เน€เธกเธฉเธฒเธขเธ|เธเธคเธฉเธ เธฒเธเธก|เธกเธดเธ–เธธเธเธฒเธขเธ|เธเธฃเธเธเธฒเธเธก|เธชเธดเธเธซเธฒเธเธก|เธเธฑเธเธขเธฒเธขเธ|เธ•เธธเธฅเธฒเธเธก|เธเธคเธจเธเธดเธเธฒเธขเธ|เธเธฑเธเธงเธฒเธเธก)\s+(\d{3,4})',
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
    if not target_id or not message: return jsonify({"ok": False, "error": "เธ•เนเธญเธเธฃเธฐเธเธธ targetId เนเธฅเธฐ message"}), 400
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
                    try: line_bot_api.push_message(target, TextSendMessage(text="๐‘ Auto-End: Showtime เธซเธกเธ”เธงเธฑเธเธ—เธตเนเธ•เธฑเนเธเนเธงเนเนเธฅเนเธง"))
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
                line_bot_api.push_message(target, TextSendMessage(text=f"๐ค Showtime Now: {artist}\nโฑ๏ธ {time_range}\n๐ช {event_name}"))
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

                tag = exp.get('tag') or '#เธ—เธฑเนเธงเนเธ'
                if tag not in categories: categories[tag] = {'total_thb': 0.0, 'participants': set()}
                categories[tag]['total_thb'] += amt_thb

                ppl = exp.get('participants') or []
                if isinstance(ppl, str): ppl = [p.strip() for p in ppl.split() if p.strip()]
                if not ppl:
                    payer = exp.get('payer_name') or get_display_name(exp['line_user_id'], trip.get('line_group_id'))
                    ppl = [payer]
                for p in ppl:
                    if p: categories[tag]['participants'].add(str(p))

            msg = f"๐“ เธชเธฃเธธเธเธขเธญเธ”เธเธฃเธฐเธเธณเธงเธฑเธ ({today_str})\n๐€ เธ—เธฃเธดเธ: {trip['title']}\n\n"
            if currency_totals:
                cur_lines = [f"{c} {float(v):,.2f}" for c, v in sorted(currency_totals.items())]
                msg += "๐’ฑ เธขเธญเธ”เธ•เธฒเธกเธชเธเธธเธฅ: " + " | ".join(cur_lines) + "\n"
            msg += f"๐’ต เธขเธญเธ”เธฃเธงเธกเธงเธฑเธเธเธตเน (THB): {total_thb:,.2f} เธเธฒเธ—\n\n"
            for tag, data in sorted(categories.items()):
                ppl_str = " ".join(sorted(data['participants']))
                msg += f"{tag} {data['total_thb']:,.0f} เธ. ({ppl_str})\n"

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

    # --- Follow-up states for Slip ---
    if state and state.get("action") == "wait_slip_payer":
        payer_name = text
        if payer_name in ["เธเธฑเธ", "เธเธฑเธเน€เธญเธ", "me"]: payer_name = get_display_name(user_id, state.get('group_id'))
        set_state(user_id, {"action": "wait_slip_participants", "message_id": state["message_id"], "trip_id": state["trip_id"], "group_id": state.get("group_id"), "payer_name": payer_name})
        line_bot_api.reply_message(reply_token, TextSendMessage(text=" เธซเธฒเธฃเธเธฑเธเนเธเธฃเธเนเธฒเธ?\nเธเธดเธกเธเนเธเธทเนเธญเธเธฑเนเธเธ”เนเธงเธขเน€เธงเนเธเธงเธฃเธฃเธ เน€เธเนเธ: เธเธญเธฅ เธเธฒเธ เน€เธญเนเธก"))
        return

    if state and state.get("action") == "wait_slip_participants":
        names = [n.strip() for n in text.split() if n.strip()]
        if not names:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="โ ๏ธ เธเธฃเธธเธ“เธฒเธเธดเธกเธเนเธฃเธฒเธขเธเธทเนเธญเธญเธขเนเธฒเธเธเนเธญเธข 1 เธเธ"))
            return
        my_name = get_display_name(user_id, state.get('group_id'))
        names = [my_name if n in ["เธเธฑเธ", "เธเธฑเธเน€เธญเธ", "me"] else n for n in names]
        payer_name = state.get("payer_name")
        threading.Thread(target=process_slip_with_payer, args=(state["message_id"], state["trip_id"], user_id, state.get("group_id"), reply_token, payer_name, names)).start()
        clear_state(user_id)
        return

    # --- Follow-up states for Expense ---
    if state and state.get("action") == "wait_expense_participants":
        names = [n.strip() for n in text.split() if n.strip()]
        if not names:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="โ ๏ธ เธเธฃเธธเธ“เธฒเธเธดเธกเธเนเธฃเธฒเธขเธเธทเนเธญเธญเธขเนเธฒเธเธเนเธญเธข 1 เธเธ"))
            return
        my_name = get_display_name(user_id, state.get("group_id"))
        names = [my_name if n in ["เธเธฑเธ", "เธเธฑเธเน€เธญเธ", "me"] else n for n in names]
        if not supabase: return
        try:
            supabase.table("expenses").insert({"trip_id": state["trip_id"], "line_user_id": user_id, "payer_name": state["payer_name"], "amount": state["amount"], "item_name": state["item"], "currency": state["currency"], "tag": state["tag"], "participants": names, "slip_url": None}).execute()
            curr_txt = f" {state['currency']}" if state['currency'] != "THB" else ""
            tag_txt = f" ({state['tag']})" if state['tag'] else ""
            ppl_txt = " ".join(names)
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"โ… เธเธฑเธเธ—เธถเธ {state['amount']:,.2f}{curr_txt}{tag_txt}\nเธเนเธฒเธข: {state['payer_name']}\nเธซเธฒเธฃ: {ppl_txt}"))
        except Exception as e: logger.error(f"Save expense error: {e}")
        clear_state(user_id)
        return

    # --- Showtime Mode Handling ---
    if state and state.get("action") == "showtime_mode":
        if text_lower == "save":
            if state.get("showtime_temp"):
                event_name = state.get("event_name")
                existing = load_showtime(event_name)
                existing["schedule"] = sort_showtime_by_time(state["showtime_temp"])
                existing["last_updated"] = datetime.now().isoformat()
                ok = save_showtime(existing, event_name)
                if not ok:
                    line_bot_api.reply_message(reply_token, TextSendMessage(text="Save showtime failed (DB permission denied)."))
                    return
            clear_state(user_id)
            line_bot_api.reply_message(reply_token, TextSendMessage(text="Showtime saved."))
            return

        if text_lower in ["end showtime", "stop showtime"]:
            events = list_showtime_events()
            if not events:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="โ ๏ธ เนเธกเนเธกเธต event เนเธเธฃเธฐเธเธเนเธซเนเธฅเธ"))
                return
            msg = "๐—‘๏ธ เน€เธฅเธทเธญเธ event เธ—เธตเนเธ•เนเธญเธเธเธฒเธฃเธฅเธ:\n"
            for i, ev in enumerate(events, 1):
                msg += f"{i}. ๐ช {ev['event_name']} ({ev['count']} เธงเธ)\n"
            msg += "\nเธเธดเธกเธเน 'end showtime [เน€เธฅเธ]' เน€เธเนเธ end showtime 1\nเธซเธฃเธทเธญ 'exit' เน€เธเธทเนเธญเธญเธญเธเธเธฒเธเนเธซเธกเธ”"
            set_state(user_id, {"action": "wait_end_showtime_event_index", "events": events, "target_id": group_id or user_id, "group_id": group_id})
            line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
            return

        # --- menu/help/exit เธ•เนเธญเธ bypass เธ—เธธเธ state เนเธ showtime_mode (เธฃเธงเธก edit_mode) ---
        if text in ["เน€เธกเธเธน", "menu", "help"]:
            line_bot_api.reply_message(reply_token, build_showtime_menu_flex(state.get("show_date")))
            return

        if text_lower in ["exit", "เธญเธญเธ"]:
            clear_state(user_id)
            line_bot_api.reply_message(reply_token, TextSendMessage(text="โ… เธญเธญเธเธเธฒเธเนเธซเธกเธ” Showtime เน€เธฃเธตเธขเธเธฃเนเธญเธข\n๐“ธ เธเธฅเธฑเธเธกเธฒเธฃเธฑเธเธชเธฅเธดเธเธเธเธ•เธดเนเธฅเนเธงเธเธฃเธฑเธ"))
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
                existing["last_updated"] = datetime.now().isoformat()
                save_showtime(existing, event_name)
                line_bot_api.reply_message(reply_token, TextSendMessage(text="โ… เธญเธฑเธเน€เธ”เธ• Showtime เน€เธชเธฃเนเธ!\n\n" + format_showtime_message(state.get("show_date"), event_name) + "\n\nเธเธดเธกเธเน 'save' เน€เธเธทเนเธญเธเธ"))
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="โ ๏ธ เนเธกเนเธเธเธฃเธนเธเนเธเธเธ—เธตเนเธ–เธนเธเธ•เนเธญเธ\nเธ•เธฑเธงเธญเธขเนเธฒเธ: 13:00-13:50 ROMANCE"))
            return

        # เธฃเธฑเธ Showtime เธเนเธฒเธเธเนเธญเธเธงเธฒเธก (Pattern: เน€เธงเธฅเธฒ-เน€เธงเธฅเธฒ เธเธทเนเธญ)
        time_pattern_input = r'^(\d{1,2}[:.]\d{2})\s*[-โ€“]\s*(\d{1,2}[:.]\d{2})\s+(.+)$'
        match = re.match(time_pattern_input, text)
        if match:
            time_input = f"{match.group(1)}-{match.group(2)}".replace('.', ':')
            artist_input = match.group(3).strip()
            if "showtime_temp" not in state: state["showtime_temp"] = []
            found = False
            for item in state["showtime_temp"]:
                if item["time"] == time_input: item["artist"] = artist_input; found = True; break
            if not found: state["showtime_temp"].append({"time": time_input, "artist": artist_input})
            state["showtime_temp"] = sort_showtime_by_time(state["showtime_temp"])
            set_state(user_id, state)
            msg = f"โ… เน€เธเธดเนเธก Showtime: {time_input} | {artist_input}\n\n๐“ เธ•เธฒเธฃเธฒเธเธเธฑเนเธงเธเธฃเธฒเธง:\n"
            for item in state["showtime_temp"]: msg += f"โฑ๏ธ {item['time']} | ๐ค {item['artist']}\n"
            msg += "\n๐‘ เธชเนเธเน€เธเธดเนเธกเธญเธตเธ เธซเธฃเธทเธญเธเธดเธกเธเน 'save'"
            line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
            return

        if text_lower == "showtime":
            line_bot_api.reply_message(reply_token, [TextSendMessage(text=format_showtime_message(state.get("show_date"), state.get("event_name"))), build_showtime_menu_flex(state.get("show_date"))])
            return
        if text_lower in ["editshowtime", "update showtime"]:
            set_state(user_id, {**state, "edit_mode": True})
            line_bot_api.reply_message(reply_token, TextSendMessage(text="โ๏ธ เนเธเนเนเธ Showtime\n" + format_showtime_message(state.get("show_date"), state.get("event_name"))))
            return

        allowed = ["save", "showtime", "editshowtime", "update showtime", "เน€เธกเธเธน", "menu", "help", "เธขเธเน€เธฅเธดเธ", "cancel", "end showtime", "exit", "เธญเธญเธ"]
        if re.match(r'^end showtime\s+\d+$', text_lower): allowed.append(text_lower)
        if text_lower not in allowed and text not in allowed:
            if not re.match(time_pattern_input, text):
                line_bot_api.reply_message(reply_token, TextSendMessage(text="๐”’ เธ•เธญเธเธเธตเนเธญเธขเธนเนเนเธเนเธซเธกเธ” Showtime\nเธเธดเธกเธเน 'menu' เน€เธเธทเนเธญเธ”เธนเธเธณเธชเธฑเนเธ เธซเธฃเธทเธญเธชเนเธ: 22:00-23:00 เธเธทเนเธญเธงเธ"))
            return

    if state and state.get("action") == "wait_end_showtime_event_index":
        if text_lower in ["exit", "เธญเธญเธ"]:
            clear_state(user_id)
            line_bot_api.reply_message(reply_token, TextSendMessage(text="โ… เธญเธญเธเธเธฒเธเนเธซเธกเธ”เธฅเธ event เนเธฅเนเธง"))
            return
        m = re.match(r"^end\s+showtime\s+(\d+)$", text_lower)
        if not m:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="เธเธดเธกเธเน 'end showtime [เน€เธฅเธ]' เธซเธฃเธทเธญ 'exit'"))
            return
        events = state.get("events", [])
        idx = int(m.group(1))
        if idx < 1 or idx > len(events):
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"โ ๏ธ เน€เธฅเธเนเธกเนเธ–เธนเธเธ•เนเธญเธ (1-{len(events)})"))
            return
        selected = events[idx - 1]
        set_state(user_id, {"action": "wait_end_showtime_confirm", "event_name": selected["event_name"]})
        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"เธขเธทเธเธขเธฑเธเธฅเธ event '{selected['event_name']}' เธ—เธฑเนเธเธซเธกเธ”?\nเธเธดเธกเธเน 'yes' เน€เธเธทเนเธญเธขเธทเธเธขเธฑเธ เธซเธฃเธทเธญ 'exit' เน€เธเธทเนเธญเธขเธเน€เธฅเธดเธ"))
        return

    if state and state.get("action") == "wait_end_showtime_confirm":
        if text_lower == "yes":
            event_name = state.get("event_name")
            ok = delete_showtime_event(event_name)
            clear_state(user_id)
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"โ… เธฅเธ event '{event_name}' เน€เธฃเธตเธขเธเธฃเนเธญเธข" if ok else "โ เธฅเธ event เนเธกเนเธชเธณเน€เธฃเนเธ"))
            return
        if text_lower in ["exit", "เธญเธญเธ"]:
            clear_state(user_id)
            line_bot_api.reply_message(reply_token, TextSendMessage(text="โ… เธขเธเน€เธฅเธดเธเธเธฒเธฃเธฅเธ event เนเธฅเนเธง"))
            return
        line_bot_api.reply_message(reply_token, TextSendMessage(text="เธเธดเธกเธเน 'yes' เน€เธเธทเนเธญเธขเธทเธเธขเธฑเธ เธซเธฃเธทเธญ 'exit' เน€เธเธทเนเธญเธขเธเน€เธฅเธดเธ"))
        return

    # --- Entry: Start Showtime ---
    if text_lower == "showtime":
        events = list_showtime_events()
        if events:
            msg = build_showtime_event_list_message()
            msg += "\n\nเธเธดเธกเธเน 'เน€เธเธดเนเธก' เน€เธเธทเนเธญเน€เธเธดเนเธกเธ•เธฒเธฃเธฒเธเนเธซเธกเน\nเธซเธฃเธทเธญเธเธดเธกเธเน 'showtime [เน€เธฅเธ]' เน€เธเธทเนเธญเธ”เธนเธ•เธฒเธฃเธฒเธเน€เธ”เธดเธก"
            set_state(user_id, {"action": "wait_showtime_add_confirm", "events": events})
            line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
            return
        set_state(user_id, {"action": "wait_showtime_event_name"})
        line_bot_api.reply_message(reply_token, TextSendMessage(text="เธเธฃเธธเธ“เธฒเธฃเธฐเธเธธเธเธทเนเธญ Showtime/Event\nเน€เธเนเธ Big Mountain 2026"))
        return

    if state and state.get("action") == "wait_showtime_add_confirm":
        if text in ["เมนู", "menu", "help"]:
            line_bot_api.reply_message(reply_token, build_main_menu_flex())
            return
        if text_lower in ["ยกเลิก", "cancel", "exit", "ออก"]:
            clear_state(user_id)
            line_bot_api.reply_message(reply_token, TextSendMessage(text="? ยกเลิกโหมด Showtime แล้ว"))
            return
        events = state.get("events", [])
        if text_lower in ["เน€เธเธดเนเธก", "add", "yes", "y"]:
            set_state(user_id, {"action": "wait_showtime_event_name"})
            line_bot_api.reply_message(reply_token, TextSendMessage(text="เธเธฃเธธเธ“เธฒเธฃเธฐเธเธธเธเธทเนเธญ Showtime/Event"))
            return
        m = re.match(r"^showtime\\s+(\\d+)$", text_lower)
        if m:
            idx = int(m.group(1))
            if idx < 1 or idx > len(events):
                line_bot_api.reply_message(reply_token, TextSendMessage(text=f"เน€เธฅเธเนเธกเนเธ–เธนเธเธ•เนเธญเธ (1-{len(events)})"))
                return
            selected = events[idx - 1]
            msg = format_showtime_message(None, selected["event_name"])
            msg += "\n\nเธเธดเธกเธเน 'เน€เธเธดเนเธก' เธ–เนเธฒเธ•เนเธญเธเธเธฒเธฃเน€เธเธดเนเธกเธ•เธฒเธฃเธฒเธเนเธซเธกเน"
            line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
            return
        line_bot_api.reply_message(reply_token, TextSendMessage(text="เธเธดเธกเธเน 'เน€เธเธดเนเธก' เธซเธฃเธทเธญ 'showtime [เน€เธฅเธ]'"))
        return

    if state and state.get("action") == "wait_showtime_event_name":
        if text in ["เมนู", "menu", "help"]:
            line_bot_api.reply_message(reply_token, build_main_menu_flex())
            return
        if text_lower in ["ยกเลิก", "cancel", "exit", "ออก"]:
            clear_state(user_id)
            line_bot_api.reply_message(reply_token, TextSendMessage(text="? ยกเลิกโหมด Showtime แล้ว"))
            return
        event_name = text.strip()
        if not event_name:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="โ ๏ธ เธเธทเนเธญ event เธซเนเธฒเธกเธงเนเธฒเธ"))
            return
        set_state(user_id, {"action": "wait_showtime_date", "event_name": event_name})
        line_bot_api.reply_message(reply_token, TextSendMessage(text="๐“… เธเธฃเธธเธ“เธฒเธฃเธฐเธเธธเธงเธฑเธเธ—เธตเนเธเธฑเธ”เนเธชเธ”เธ (YYYY-MM-DD)\nเน€เธเนเธ 2026-05-30\n(เธเธดเธกเธเน 'เธเนเธฒเธก' เธซเธฒเธเนเธกเนเธ•เนเธญเธเธเธฒเธฃเธเธณเธซเธเธ”)"))
        return

    if state and state.get("action") == "wait_showtime_date":
        if text in ["เมนู", "menu", "help"]:
            line_bot_api.reply_message(reply_token, build_main_menu_flex())
            return
        if text_lower in ["ยกเลิก", "cancel", "exit", "ออก"]:
            clear_state(user_id)
            line_bot_api.reply_message(reply_token, TextSendMessage(text="? ยกเลิกโหมด Showtime แล้ว"))
            return
        show_date = None
        if text_lower != "เธเนเธฒเธก" and text_lower != "skip":
            try:
                parsed = datetime.strptime(text.strip(), "%Y-%m-%d")
                show_date = parsed.strftime("%Y-%m-%d")
            except ValueError:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="โ ๏ธ เธฃเธนเธเนเธเธเธงเธฑเธเธ—เธตเนเนเธกเนเธ–เธนเธเธ•เนเธญเธ เนเธเน YYYY-MM-DD เธซเธฃเธทเธญเธเธดเธกเธเน 'เธเนเธฒเธก'"))
                return
        event_name = state.get("event_name")
        set_state(user_id, {"action": "showtime_mode", "event_name": event_name, "show_date": show_date, "end_date": show_date, "edit_mode": False, "target_id": group_id or user_id, "group_id": group_id, "last_alert_key": None})
        msg = format_showtime_message(show_date, event_name) + "\n\nเธชเนเธเธฃเธนเธเธซเธฃเธทเธญเธเธดเธกเธเนเธ•เธฒเธฃเธฒเธเนเธชเธ”เธ เนเธฅเนเธงเธเธดเธกเธเน 'save' เน€เธเธทเนเธญเธเธฑเธเธ—เธถเธ"
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return

    # --- Normal Commands ---
    if text in ["เน€เธกเธเธน", "menu", "help"]:
        line_bot_api.reply_message(reply_token, build_main_menu_flex())
        return

    if text in ["เธขเธเน€เธฅเธดเธ", "cancel"]:
        if state:
            clear_state(user_id)
            line_bot_api.reply_message(reply_token, TextSendMessage(text="โ… เธขเธเน€เธฅเธดเธเน€เธฃเธตเธขเธเธฃเนเธญเธข"))
        return

    if text_lower.startswith("edit"):
        trip = get_active_trip(user_id, group_id)
        if not trip:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="โ ๏ธ เนเธกเนเธกเธตเธ—เธฃเธดเธเธ—เธตเนเธเธณเธฅเธฑเธเธ—เธณเธเธฒเธเธญเธขเธนเน"))
            return
        edit_parts = text_lower.split()
        if len(edit_parts) == 3 and edit_parts[0] == "edit":
            try:
                inline_id, inline_amount = int(edit_parts[1]), float(edit_parts[2].replace(',', ''))
                if inline_amount > 0:
                    expenses = get_all_expenses(trip['id'])
                    selected = next((e for e in expenses if e['id'] == inline_id), None)
                    if selected and update_expense_amount(inline_id, inline_amount):
                        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"โ… เนเธเนเนเธเธฃเธฒเธขเธเธฒเธฃ ID {inline_id:04d} เน€เธเนเธ {inline_amount:,.2f} เธเธฒเธ— เน€เธฃเธตเธขเธเธฃเนเธญเธข!"))
                    else: line_bot_api.reply_message(reply_token, TextSendMessage(text="โ เนเธเนเนเธเนเธกเนเธชเธณเน€เธฃเนเธ"))
                return
            except: pass
        expenses = get_all_expenses(trip['id'])
        if not expenses:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="โ ๏ธ เนเธกเนเธกเธตเธฃเธฒเธขเธเธฒเธฃเธเนเธฒเนเธเนเธเนเธฒเธขเนเธซเนเนเธเนเนเธ"))
            return
        lines_edit = [f"ID {exp['id']:04d} | {exp['item_name'][:30]} | {exp['amount']:,.2f} เธเธฒเธ—" for exp in expenses]
        line_bot_api.reply_message(reply_token, build_report_flex(title="โ๏ธ เน€เธฅเธทเธญเธเธฃเธฒเธขเธเธฒเธฃเนเธเนเนเธ", subtitle="เธเธดเธกเธเน: edit [ID] [เธขเธญเธ”เนเธซเธกเน]", lines=lines_edit, alt_text="เนเธเนเนเธเธฃเธฒเธขเธเธฒเธฃ"))
        return

    if text.startswith("เธ—เธฃเธดเธ ") or text_lower.startswith("trip "):
        trip_name = text[5:].strip() if text.startswith("เธ—เธฃเธดเธ ") else text[5:].strip()
        if not trip_name:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="โ ๏ธ เธเธฃเธธเธ“เธฒเธฃเธฐเธเธธเธเธทเนเธญเธ—เธฃเธดเธ"))
            return
        if not supabase: return
        try:
            supabase.table("trips").update({"status": "closed"}).eq("creator_id", user_id).execute()
            supabase.table("trips").insert({"title": trip_name, "status": "active", "line_group_id": group_id, "creator_id": user_id}).execute()
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"๐€ เน€เธฃเธดเนเธกเธ—เธฃเธดเธเนเธซเธกเน: {trip_name} เน€เธฃเธตเธขเธเธฃเนเธญเธข!"))
        except Exception as e:
            logger.error(f"Create trip error: {e}")
            line_bot_api.reply_message(reply_token, TextSendMessage(text=" เธชเธฃเนเธฒเธเธ—เธฃเธดเธเนเธกเนเธชเธณเน€เธฃเนเธ"))
        return

    if text == "id":
        msg = f" User ID: {user_id}"
        if group_id: msg += f"\n๐‘ฅ Group ID: {group_id}"
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return

    if text == "เธขเธญเธ”" or text_lower == "sum":
        trip = get_active_trip(user_id, group_id)
        if not trip:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="โ ๏ธ เนเธกเนเธกเธตเธ—เธฃเธดเธเธ—เธตเนเธเธณเธฅเธฑเธเธ—เธณเธเธฒเธเธญเธขเธนเน"))
            return
        expenses = get_all_expenses(trip['id'])
        if not expenses:
            line_bot_api.reply_message(reply_token, TextSendMessage(text=" เธขเธฑเธเนเธกเนเธกเธตเธฃเธฒเธขเธเธฒเธฃเธเนเธฒเนเธเนเธเนเธฒเธข"))
            return
        total_thb = 0
        categories = {}
        for exp in expenses:
            amt_thb = exp['amount']
            curr = exp.get('currency', 'THB')
            if curr != 'THB': amt_thb *= (get_exchange_rate(curr, 'THB') or 1.0)
            total_thb += amt_thb
            tag = exp.get('tag') or '#เธ—เธฑเนเธงเนเธ'
            if tag not in categories: categories[tag] = {'total': 0, 'participants': set()}
            categories[tag]['total'] += amt_thb
            ppl = exp.get('participants') or []
            if isinstance(ppl, str): ppl = [p.strip() for p in ppl.split() if p.strip()]
            if not ppl:
                payer = exp.get('payer_name') or get_display_name(exp['line_user_id'], group_id)
                ppl = [payer]
            for p in ppl:
                if p: categories[tag]['participants'].add(str(p))
        lines = [f"เธขเธญเธ”เธฃเธงเธก {total_thb:,.2f} เธเธฒเธ—"]
        for tag, data in sorted(categories.items()):
            ppl_str = " ".join(sorted(data['participants']))
            lines.append(f"{tag} {data['total']:,.0f} {ppl_str}")
        line_bot_api.reply_message(reply_token, build_report_flex(title="๐’ฐ เธขเธญเธ”เธฃเธงเธกเธ—เธฃเธดเธ", subtitle=None, lines=lines, alt_text="เธขเธญเธ”เธฃเธงเธก"))
        return

    if text == "เธขเธญเธ”เธงเธฑเธเธเธตเน" or text_lower == "เธขเธญเธ”เธงเธฑเธเธเธตเน" or text_lower.startswith("เธขเธญเธ”เธงเธฑเธเธเธตเน "):
        parts = text.split()
        target_curr = "THB"
        if len(parts) > 1:
            t = _normalize_currency(parts[1])
            target_curr = "JPY" if t == "JYP" else (t or parts[1].upper())
        today_str = (datetime.now() + timedelta(hours=7)).strftime("%Y-%m-%d")
        trip = get_active_trip(user_id, group_id)
        if not trip:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="โ ๏ธ เนเธกเนเธกเธตเธ—เธฃเธดเธเธ—เธตเนเธเธณเธฅเธฑเธเธ—เธณเธเธฒเธเธญเธขเธนเน"))
            return
        expenses = get_all_expenses(trip['id'])
        today_exp = [e for e in expenses if _to_thai_date_str(e.get('created_at', '')) == today_str]
        if not today_exp:
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"โน๏ธ เธงเธฑเธเธเธตเน ({today_str}) เธขเธฑเธเนเธกเนเธกเธตเธฃเธฒเธขเธเนเธฒเธข"))
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
            tag = exp.get('tag') or '#เธ—เธฑเนเธงเนเธ'
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
            subtitle_parts.append("๐’ฑ เธขเธญเธ”เธ•เธฒเธกเธชเธเธธเธฅ: " + " | ".join(cur_lines))
        if target_curr != "THB":
            subtitle_parts.append(f"1 THB = {rate_to_target:.4f} {target_curr}")
            subtitle_parts.append(f" เธฃเธงเธกเธงเธฑเธเธเธตเน: {total_thb:,.2f} เธเธฒเธ— (โ {total_target:,.2f} {target_curr})")
        else:
            subtitle_parts.append(f"๐’ต เธฃเธงเธกเธงเธฑเธเธเธตเน: {total_thb:,.2f} เธเธฒเธ—")
        lines = []
        for tag, data in sorted(categories.items()):
            ppl_str = " ".join(sorted(data['participants']))
            line = f"{tag} {data['total_thb']:,.0f} {ppl_str}"
            if target_curr != "THB": line += f" (โ {data['total_thb'] * rate_to_target:,.2f} {target_curr})"
            lines.append(line)
        line_bot_api.reply_message(reply_token, build_report_flex(title=f"๐“… เธขเธญเธ”เธงเธฑเธเธเธตเน ({today_str})", subtitle="\n".join(subtitle_parts), lines=lines, alt_text="เธขเธญเธ”เธงเธฑเธเธเธตเน"))
        return

    # --- BLOCK 3: Enhanced Expense Parsing ---
    # guard: เธซเนเธฒเธก parse expense เธเธ“เธฐเธฃเธญ input เธซเธฃเธทเธญเธญเธขเธนเนเนเธ state เธญเธทเนเธ
    SLIP_STATES = {"wait_slip_payer", "wait_slip_participants", "wait_expense_participants",
                   "showtime_mode", "wait_showtime_add_confirm", "wait_showtime_event_name", "wait_showtime_date",
                   "wait_end_showtime_event_index", "wait_end_showtime_confirm", "end_trip",
                   "edit_selection", "edit_amount", "stop_event", "export_history"}
    trip = get_active_trip(user_id, group_id)
    if trip and not (state and state.get("action") in SLIP_STATES):
        default_payer = get_display_name(user_id, group_id)
        payer, item, amount, currency, tag, participants = parse_enhanced_expense(text, default_payer_name=default_payer)
        if amount and amount > 0:
            if not participants:
                set_state(user_id, {"action": "wait_expense_participants", "trip_id": trip['id'], "group_id": group_id, "payer_name": payer, "item": item, "amount": amount, "currency": currency, "tag": tag})
                line_bot_api.reply_message(reply_token, TextSendMessage(text="๐‘ฅ เธซเธฒเธฃเธเธฑเธเนเธเธฃเธเนเธฒเธ?\nเธเธดเธกเธเนเธเธทเนเธญเธเธฑเนเธเธ”เนเธงเธขเน€เธงเนเธเธงเธฃเธฃเธ เน€เธเนเธ: เธเธญเธฅ เธเธฒเธ เน€เธญเนเธก"))
                return
            if not supabase: return
            try:
                supabase.table("expenses").insert({"trip_id": trip['id'], "line_user_id": user_id, "payer_name": payer, "amount": amount, "item_name": item or "เธเนเธฒเนเธเนเธเนเธฒเธข", "currency": currency, "tag": tag, "participants": participants, "slip_url": None}).execute()
                curr_txt = f" {currency}" if currency != "THB" else ""
                tag_txt = f" ({tag})" if tag else ""
                ppl_txt = " ".join(participants or [])
                line_bot_api.reply_message(reply_token, TextSendMessage(text=f"โ… เธเธฑเธเธ—เธถเธ {amount:,.2f}{curr_txt}{tag_txt}\nเธเนเธฒเธข: {payer}\nเธซเธฒเธฃ: {ppl_txt}"))
            except Exception as e: logger.error(f"Save expense error: {e}")
        return

    # --- Other Commands (End Trip, Excel, History, Event) ---
    if text.startswith("เธเธเธ—เธฃเธดเธ ") or text_lower.startswith("end trip "):
        trip = get_active_trip(user_id, group_id)
        if not trip:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="โ ๏ธ เนเธกเนเธกเธตเธ—เธฃเธดเธเธ—เธตเนเธเธณเธฅเธฑเธเธ—เธณเธเธฒเธเธญเธขเธนเน"))
            return
        currency_code = "THB"
        if text.startswith("เธเธเธ—rip "):
            parts = text.split()
            if len(parts) >= 2: currency_code = parts[1].upper()
        elif text_lower.startswith("end trip "):
            parts = text_lower.split()
            if len(parts) >= 3: currency_code = parts[2].upper()
        set_state(user_id, {"action": "end_trip", "trip_id": trip['id'], "trip_title": trip['title'], "currency_code": currency_code})
        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"๐ เธเธดเธ”เธ—เธฃเธดเธ: {trip['title']}\n๐’ฑ เนเธเธฅเธเน€เธเนเธเธชเธเธธเธฅ: {currency_code}\n\n๐‘ฅ เธฃเธฐเธเธธเธเธณเธเธงเธเธเธเธ—เธตเนเธเธฐเธซเธฒเธฃ: "))
        return

    if state and state.get("action") == "end_trip":
        try:
            num_people = int(text)
            if num_people <= 0: raise ValueError
        except:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="โ ๏ธ เธเธฃเธธเธ“เธฒเธฃเธฐเธเธธเธเธณเธเธงเธเธเธเน€เธเนเธเธ•เธฑเธงเน€เธฅเธเธ—เธตเนเธกเธฒเธเธเธงเนเธฒ 0"))
            return
        trip_id = state["trip_id"]
        trip_title = state["trip_title"]
        currency_code = state.get("currency_code", "THB")
        total_thb, paid_totals, share_totals, people = compute_trip_balances_thb(trip_id, group_id)
        if total_thb == 0:
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"๐€ เธ—เธฃเธดเธ: {trip_title}\nโ ๏ธ เนเธกเนเธกเธตเธฃเธฒเธขเธเธฒเธฃเธเนเธฒเนเธเนเธเนเธฒเธขเนเธซเนเธซเธฒเธฃ"))
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
        msg = f"๐€ เธ—เธฃเธดเธ: {trip_title}\n๐‘ฅ เธเธณเธเธงเธเธเธ: {real_n}\n\n"
        if currency_code != "THB" and exchange_rate != 1:
            msg += f"๐’ฑ เธญเธฑเธ•เธฃเธฒเนเธฅเธเน€เธเธฅเธตเนเธขเธ: 1 THB = {exchange_rate:.4f} {currency_code}\n\n"
            msg += f"๐“ เธขเธญเธ”เธซเธฒเธฃเน€เธเธฅเธตเนเธข:\n   โ€ข {avg:,.2f} เธเธฒเธ—/เธเธ\n   โ€ข โ {avg * exchange_rate:,.2f} {currency_code}/เธเธ\n\n"
        else:
            msg += f"๐“ เธขเธญเธ”เธซเธฒเธฃเน€เธเธฅเธตเนเธข: {avg:,.2f} เธเธฒเธ—/เธเธ\n\n"
        msg += "๐’ต เธขเธญเธ”เธชเธฃเธธเธเธชเธธเธ—เธเธด (เธเนเธฒเธขเน€เธเธดเนเธก/เธฃเธฑเธเธเธทเธ):\n"
        for name in real_people:
            paid = paid_totals.get(name, 0.0)
            share = share_totals.get(name, 0.0)
            diff = paid - share
            if currency_code != "THB" and exchange_rate != 1:
                if diff > 0: msg += f"โ€ข {name}: เธฃเธฑเธเธเธทเธ {diff:,.2f} เธเธฒเธ— (โ {diff * exchange_rate:,.2f} {currency_code})\n"
                elif diff < 0: msg += f"โ€ข {name}: เธเนเธฒเธขเน€เธเธดเนเธก {abs(diff):,.2f} เธเธฒเธ— (โ {abs(diff) * exchange_rate:,.2f} {currency_code})\n"
                else: msg += f"โ€ข {name}: เน€เธฃเธตเธขเธเธฃเนเธญเธขเนเธฅเนเธง\n"
            else:
                if diff > 0: msg += f"โ€ข {name}: เธฃเธฑเธเธเธทเธ {diff:,.2f} เธเธฒเธ—\n"
                elif diff < 0: msg += f"โ€ข {name}: เธเนเธฒเธขเน€เธเธดเนเธก {abs(diff):,.2f} เธเธฒเธ—\n"
                else: msg += f"โ€ข {name}: เน€เธฃเธตเธขเธเธฃเนเธญเธขเนเธฅเนเธง\n"
        try:
            if supabase: supabase.table("trips").update({"status": "closed", "currency_code": currency_code}).eq("id", trip_id).execute()
        except Exception as e: logger.error(f"Close trip error: {e}")
        clear_state(user_id)
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return

    if text_lower == "excel":
        trip = get_active_trip(user_id, group_id)
        if not trip:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="โ ๏ธ เนเธกเนเธกเธตเธ—เธฃเธดเธเธ—เธตเนเธเธณเธฅเธฑเธเธ—เธณเธเธฒเธเธญเธขเธนเน"))
            return
        excel_buffer, error = export_trip_to_excel(trip['id'], trip['title'])
        if error:
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"โ เนเธกเนเธชเธฒเธกเธฒเธฃเธ–เธชเธฃเนเธฒเธ Excel: {error}"))
            return
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{trip['title']}_{timestamp}.xlsx"
        public_url, upload_error = upload_excel_to_supabase(excel_buffer, filename)
        if upload_error:
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"โ เธญเธฑเธเนเธซเธฅเธ”เธฅเนเธกเน€เธซเธฅเธง: {upload_error}"))
            return
        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"โ… เธชเธฃเนเธฒเธเนเธเธฅเน Excel เธชเธณเน€เธฃเนเธ!\n๐“ เธ—เธฃเธดเธ: {trip['title']}\n๐”— {public_url}"))
        return

    if text in ["เธเธฃเธฐเธงเธฑเธ•เธด", "history"]:
        if not supabase: return
        try:
            res = supabase.table("trips").select("*").order("created_at", desc=True).limit(10).execute()
            all_trips = res.data if res.data else []
            if not all_trips:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="๐“ เธขเธฑเธเนเธกเนเธกเธตเธเธฃเธฐเธงเธฑเธ•เธดเธ—เธฃเธดเธ"))
                return
            msg = "๐“ เธเธฃเธฐเธงเธฑเธ•เธดเธ—เธฃเธดเธ (10 เธ—เธฃเธดเธเธฅเนเธฒเธชเธธเธ”):\n\n"
            for i, trip in enumerate(all_trips, 1):
                status_icon = "๐ข" if trip['status'] == 'active' else "๐”ด"
                start_date = trip.get('created_at', '')[:10]
                end_date = trip.get('updated_at', '')[:10] if trip['status'] == 'closed' else "เธขเธฑเธเนเธกเนเธเธ"
                msg += f"{i}. {status_icon} {trip['title']}\n   ๐“… {start_date} โ’ {end_date}\n   ๐‘ เธเธดเธกเธเน: excel {i}\n\n"
            set_state(user_id, {"action": "export_history", "trips": all_trips})
            line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        except Exception as e:
            logger.error(f"History error: {e}")
            line_bot_api.reply_message(reply_token, TextSendMessage(text="โ เนเธกเนเธชเธฒเธกเธฒเธฃเธ–เธ”เธถเธเธเนเธญเธกเธนเธฅเนเธ”เน"))
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
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"โ… Excel เธชเธณเน€เธฃเนเธ!\n๐“ {selected['title']}\n๐”— {public_url}"))
                else:
                    line_bot_api.reply_message(reply_token, TextSendMessage(text=f"โ ๏ธ เธซเธกเธฒเธขเน€เธฅเธเนเธกเนเธ–เธนเธเธ•เนเธญเธ (เธกเธต 1-{len(trips)})"))
            except ValueError:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="โ ๏ธ เธเธฃเธธเธ“เธฒเธเธดเธกเธเนเธ•เธฑเธงเน€เธฅเธเน€เธ—เนเธฒเธเธฑเนเธ เน€เธเนเธ excel 1"))
            except Exception as e:
                logger.error(f"Export history error: {e}")
                line_bot_api.reply_message(reply_token, TextSendMessage(text=f"โ เน€เธเธดเธ”เธเนเธญเธเธดเธ”เธเธฅเธฒเธ”: {str(e)}"))
            clear_state(user_id)
            return
        else:
            clear_state(user_id)

    if text_lower == "event":
        events = get_active_events()
        base_url = "https://line-chat-bot-trip-manager.onrender.com"
        if not events:
            msg = f"๐” เธ•เธฃเธงเธเธชเธญเธเธฃเธฒเธขเธเธทเนเธญเธเธดเธง Event เธเธฑเธเธเธธเธเธฑเธ...\n=======================\nโน๏ธ เนเธกเนเธกเธตเธเธดเธง Event เธ—เธตเนเน€เธเธดเธ”เธญเธขเธนเน\n-----------------------\n\n๐’ป เธฅเธดเธเธเนเธเธงเธเธเธธเธกเนเธเธเธฃเธฐเธเธ:\n{base_url}"
        else:
            msg = "๐” เธ•เธฃเธงเธเธชเธญเธเธฃเธฒเธขเธเธทเนเธญเธเธดเธง Event เธเธฑเธเธเธธเธเธฑเธ...\n=======================\n"
            for i, e in enumerate(events, 1):
                msg += f"{i}. เธเธฒเธ: {e.get('name', '-')}\nโฐ เน€เธงเธฅเธฒเธเธฒเธข: {e.get('saleTime', '-')}\n เธฅเธดเธเธเนเธเธฒเธ: {e.get('url', '-')}\n-----------------------\n"
            msg += f"\n๐’ป เธฅเธดเธเธเนเธเธงเธเธเธธเธกเนเธเธเธฃเธฐเธเธ:\n{base_url}"
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return

    if text_lower == "stop event":
        events = get_active_events()
        if not events:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="โ ๏ธ เนเธกเนเธกเธต Event เธ—เธตเนเธเธณเธฅเธฑเธเธ—เธณเธเธฒเธเธญเธขเธนเน"))
            return
        set_state(user_id, {"action": "stop_event", "events": events})
        msg = "๐ซ เน€เธฅเธทเธญเธเธซเธกเธฒเธขเน€เธฅเธ Event เธ—เธตเนเธเธธเธ“เธ•เนเธญเธเธเธฒเธฃเธชเธฑเนเธเธซเธขเธธเธ”เธ—เธณเธเธฒเธ (Stop):\n=======================\n"
        for i, e in enumerate(events, 1):
            msg += f"{i}. เธเธฒเธ: {e.get('name', '-')}\n๐‘ (ID: {e.get('id', '-')})\n-----------------------\n"
        msg += " เธเธดเธกเธเนเน€เธเธเธฒเธฐ [เธ•เธฑเธงเน€เธฅเธเธฅเธณเธ”เธฑเธ] เน€เธเธทเนเธญเธฃเธฐเธเธธเน€เธฅเธทเธญเธเธเธดเธ”เธเธฒเธเธเธดเนเธเธเธฑเนเธเนเธ”เนเน€เธฅเธขเธเธฃเธฑเธ"
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
                line_bot_api.reply_message(reply_token, TextSendMessage(text=f"โ… เธชเธฑเนเธเธเธดเธ”เธเธฒเธเน€เธฃเธตเธขเธเธฃเนเธญเธขเนเธฅเนเธง!\n๐‘ เธชเธฑเนเธเธซเธขเธธเธ”เธ เธฒเธฃเธเธดเธเธเธฒเธ: {selected.get('name', '-')}"))
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="โ ๏ธ เธซเธกเธฒเธขเน€เธฅเธเนเธกเนเธ–เธนเธเธ•เนเธญเธ เธเธฃเธธเธ“เธฒเธฅเธญเธเนเธซเธกเน"))
        except ValueError:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="โ ๏ธ เธเธฃเธธเธ“เธฒเธเธดเธกเธเนเธซเธกเธฒเธขเน€เธฅเธเน€เธ—เนเธฒเธเธฑเนเธ"))
        except Exception as e:
            logger.error(f"Stop event error: {e}")
            line_bot_api.reply_message(reply_token, TextSendMessage(text="โ เน€เธเธดเธ”เธเนเธญเธเธดเธ”เธเธฅเธฒเธ” เธเธฃเธธเธ“เธฒเธฅเธญเธเนเธซเธกเน"))
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
                    existing["last_updated"] = datetime.now().isoformat()
                    save_showtime(existing, state.get("event_name"))
                    msg = "โ… เธญเธฑเธเน€เธ”เธ• Showtime เน€เธชเธฃเนเธ!\n\n" + format_showtime_message(state.get("show_date"), state.get("event_name"))
                else:
                    state["showtime_temp"] = showtime_list
                    msg = "โ… เธญเนเธฒเธเธเนเธญเธกเธนเธฅ Showtime เธชเธณเน€เธฃเนเธ\n\n๐“ เธ•เธฒเธฃเธฒเธเธเธฑเนเธงเธเธฃเธฒเธง:\n"
                    for item in showtime_list: msg += f"โฑ๏ธ {item['time']} |  {item['artist']}\n"
                    msg += "\n๐‘ เธเธดเธกเธเน 'save' เน€เธเธทเนเธญเธเธฑเธเธ—เธถเธ"
                line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="โ ๏ธ เนเธกเนเธเธเธเนเธญเธกเธนเธฅ Showtime เนเธเธฃเธนเธ"))
        except Exception as e:
            logger.error(f"Showtime OCR error: {e}")
            logger.error(traceback.format_exc())
            line_bot_api.reply_message(reply_token, TextSendMessage(text="โ เนเธกเนเธชเธฒเธกเธฒเธฃเธ–เธญเนเธฒเธเธฃเธนเธเนเธ”เน"))
        return

    trip = get_active_trip(user_id, group_id)
    if not trip:
        line_bot_api.reply_message(reply_token, TextSendMessage(text="โ ๏ธ เนเธกเนเธกเธตเธ—เธฃเธดเธเธ—เธตเนเธเธณเธฅเธฑเธเธ—เธณเธเธฒเธเธญเธขเธนเน"))
        return
    set_state(user_id, {"action": "wait_slip_payer", "message_id": event.message.id, "trip_id": trip['id'], "group_id": group_id})
    line_bot_api.reply_message(reply_token, TextSendMessage(text="๐งพ เธเธเธชเธฅเธดเธ/เธเธดเธฅ\nเธเธฃเธธเธ“เธฒเธเธดเธกเธเนเธเธทเนเธญเธเธเธ—เธตเนเธ•เนเธญเธเธฃเธฑเธเธเธดเธ”เธเธญเธเธขเธญเธ”เธเธตเน\n(เธซเธฃเธทเธญเธเธดเธกเธเน 'เธเธฑเธ' เน€เธเธทเนเธญเนเธเนเธเธทเนเธญเธเธธเธ“)"))

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
            result = supabase.table("expenses").insert({"trip_id": trip_id, "line_user_id": user_id, "payer_name": payer_name, "amount": amount, "item_name": f"เธเธดเธฅ {timestamp}", "currency": "THB", "tag": "#เธชเธฅเธดเธ", "participants": participants, "slip_url": f"slip_{message_id}"}).execute()
            new_id = result.data[0]['id'] if result.data else None
            ppl_txt = " ".join(participants or [])
            success_msg = f"โ… เธเธฑเธเธ—เธถเธ {amount:,.2f} เธเธฒเธ— (#เธชเธฅเธดเธ)\nเธเนเธฒเธข: {payer_name}\nเธซเธฒเธฃ: {ppl_txt}"
            if new_id: success_msg += f"\nโ๏ธ เนเธเนเนเธ: edit {new_id:04d} {amount}"
            line_bot_api.reply_message(reply_token, TextSendMessage(text=success_msg))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="โ ๏ธ เนเธกเนเธเธเธเธณเธเธงเธเน€เธเธดเธเนเธเธฃเธนเธ\n๐“ เธฅเธญเธเธเธฑเธเธ—เธถเธเธ”เนเธงเธขเธเนเธญเธเธงเธฒเธก เน€เธเนเธ 'เธเธญเธฅ เธเนเธฒเน€เธซเธฅเนเธฒ 500'"))
    except Exception as e:
        logger.error(f"Process slip with payer error: {e}")
        line_bot_api.reply_message(reply_token, TextSendMessage(text="โ เนเธกเนเธชเธฒเธกเธฒเธฃเธ–เธญเนเธฒเธเธฃเธนเธเนเธ”เน"))

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


