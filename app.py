import os
import re
import json
import logging
import threading
import requests
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
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=10)
        html_text = resp.text
        time_patterns = [
            r'(\d{1,2})\s+(มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|กรกฎาคม|สิงหาคม|กันยายน|ตุลาคม|พฤศจิกายน|ธันวาคม)\s+(\d{3,4})',
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
# [อัปเดตล่าสุด 2026-05-21]: API จัดการ schedules (GET, POST, DELETE)
# =================================================================
@app.route("/api/schedules", methods=["GET", "POST"])
def handle_schedules():
    if request.method == "GET":
        return jsonify({"ok": True, "schedules": load_schedules()})
    elif request.method == "POST":
        try:
            data = request.json
            schedules = load_schedules()
            new_id = str(len(schedules) + 1)
            new_schedule = {
                "id": new_id,
                "targetId": data.get("targetId", ""),
                "buyerName": data.get("buyerName", ""),
                "name": data.get("name", ""),
                "url": data.get("url", ""),
                "saleTime": data.get("saleTime", ""),
                "site": data.get("site", ""),
                "reminders": [],
                "active": True,
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
# [อัปเดตล่าสุด 2026-05-21]: แก้ไขฟังก์ชัน get_active_trip ให้ใช้ group_id หรือ user_id
# =================================================================
def get_active_trip(user_id, group_id=None):
    """ดึงทริปที่กำลังทำงานอยู่ - ถ้าอยู่ในกลุ่มให้ดึงทริปของกลุ่มนั้น ถ้าแชทส่วนตัวให้ดึงทริปที่ user สร้าง"""
    try:
        if group_id:
            res = supabase.table("trips").select("*").eq("status", "active").eq("line_group_id", group_id).execute()
            if res.data:
                return res.data[0]
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
    except:
        return user_id[:8]

def get_all_expenses(trip_id):
    """ดึงรายการค่าใช้จ่ายเฉพาะ trip_id ที่ระบุ และเพิ่มลำดับภายในทริป (seq_id)"""
    try:
        res = supabase.table("expenses").select("*").eq("trip_id", trip_id).execute()
        expenses = res.data if res.data else []
        expenses.sort(key=lambda x: x['id'])
        for idx, exp in enumerate(expenses, 1):
            exp['seq_id'] = idx
        return expenses
    except Exception as e:
        logger.error(f"Get all expenses error: {e}")
        return []

def update_expense_amount(expense_id, new_amount):
    try:
        supabase.table("expenses").update({"amount": new_amount}).eq("id", expense_id).execute()
        return True
    except Exception as e:
        logger.error(f"Update expense error: {e}")
        return False

def send_menu(reply_token):
    quick_reply = QuickReply(items=[
        QuickReplyButton(action=MessageAction(label="📋 ID", text="id")),
        QuickReplyButton(action=MessageAction(label="🚀 สร้างทริป", text="ทริป")),
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
    msg += "🚀 **ทริป** - กดแล้วพิมพ์ชื่อทริป (เช่น 'ทริป ภูเก็ต')\n"
    msg += "💰 **ยอด** - แสดงยอดรวมค่าใช้จ่าย\n"
    msg += "🏁 **จบทริป** - ปิดทริปและคำนวณหาร\n"
    msg += "✏️ **edit** - แก้ไขยอดเงิน (แสดงรายการเรียงตามยอดน้อยไปมาก พร้อมลำดับที่)\n"
    msg += "✏️ **edit [ลำดับที่] [จำนวน]** - แก้ไขทันที เช่น edit 2 500\n"
    msg += "📅 **event** - แสดง Event ที่ตั้งค่าไว้\n"
    msg += "🛑 **stop event** - หยุดการแจ้งเตือน Event\n"
    msg += "📸 **ส่งรูปสลิป/บิล** - OCR อ่านยอดอัตโนมัติ\n"
    msg += "✏️ **พิมพ์ข้อความ** เช่น 'บอล ค่าเบียร์ 2000' - บันทึกค่าใช้จ่าย\n"
    msg += "❌ **ยกเลิก** - ออกจากโหมดแก้ไข\n\n"
    msg += "💡 **คำแนะนำ**: รายการจะเรียงตามลำดับที่เพิ่ม และแสดงลำดับที่ 1,2,3..."
    
    line_bot_api.reply_message(
        reply_token,
        TextSendMessage(text=msg, quick_reply=quick_reply)
    )

# =================================================================
# [อัปเดตล่าสุด 2026-05-21]: เพิ่มระบบรองรับการอ่านยอดรวมจาก 'บิล' และ 'ใบเสร็จ'
# เพิ่มคีย์เวิร์ดยอดรวมสุทธิท้ายบิล (เช่น รวมทั้งสิ้น, ยอดคงเหลือ, net total, grand total)
# ย้อนสแกนจากบรรทัดล่างขึ้นบนเพื่อป้องกันการดักเจอราคารายการย่อยกลางบิล
# =================================================================
def extract_amount(text):
    if not text:
        return None
    
    lines = [line.strip().replace(' ', '') for line in text.split('\n') if line.strip()]
    
    # 1. ล็อกเป้าหมายคีย์เวิร์ดยอดรวมของ "บิล/ใบเสร็จ/ร้านอาหาร" โดยค้นหาจากล่างขึ้นบน
    bill_keywords = ['รวมทั้งสิ้น', 'ยอดรวมสุทธิ', 'จำนวนเงินทั้งสิ้น', 'ยอดชำระ', 'ยอดเงินสุทธิ', 'nettotal', 'grandtotal', 'totalamount', 'totaldue', 'amountdue', 'รวมเงิน']
    for i in reversed(range(len(lines))):
        line_lower = lines[i].lower()
        if any(k in line_lower for k in bill_keywords):
            search_zone = lines[i]
            if i + 1 < len(lines):
                search_zone += " " + lines[i+1]
            
            match = re.search(r'(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)', search_zone)
            if match:
                try:
                    num = float(match.group(1).replace(',', ''))
                    if 10 <= num <= 500000:
                        logger.info(f"Bill Grand Total found: {num}")
                        return num
                except:
                    continue

    # 2. คีย์เวิร์ดมาตรฐานของ "สลิปโอนเงิน" ทั่วไป
    slip_keywords = ['จำนวนเงิน', 'จำนวน', 'ยอดรวม', 'total', 'amt']
    for i, line in enumerate(lines):
        line_lower = line.lower()
        if any(k in line_lower for k in slip_keywords):
            search_zone = line
            if i + 1 < len(lines):
                search_zone += " " + lines[i+1]
            
            match = re.search(r'(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)', search_zone)
            if match:
                try:
                    num = float(match.group(1).replace(',', ''))
                    if 10 <= num <= 500000:
                        logger.info(f"Slip Amount found: {num}")
                        return num
                except:
                    continue

    # 3. เงื่อนไขดักท้ายบรรทัดที่มีหน่วยเงินบาทต่อท้ายกรณีสแกนคีย์เวิร์ดไม่เจอ
    for line in reversed(lines):
        if 'บาท' in line or 'thb' in line.lower():
            match = re.search(r'(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)', line)
            if match:
                try:
                    num = float(match.group(1).replace(',', ''))
                    if 10 <= num <= 500000:
                        return num
                except:
                    continue

    return None

def parse_expense_text(text):
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
    text = event.message.text.strip()
    text_lower = text.lower()
    user_id = event.source.user_id
    group_id = getattr(event.source, 'group_id', None)
    reply_token = event.reply_token

    if user_id in user_state and user_state[user_id].get("action") == "end_trip":
        clean_text = text.replace(',', '')
        if not clean_text.isdigit():
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาระบุจำนวนคนเป็นตัวเลขที่มากกว่า 0"))
            return
        
        try:
            num_people = int(clean_text)
            if num_people <= 0:
                raise ValueError
        except:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาระบุจำนวนคนเป็นตัวเลขที่มากกว่า 0"))
            return
        
        trip_id = user_state[user_id]["trip_id"]
        trip_title = user_state[user_id]["trip_title"]
        total, user_totals = get_total_expenses(trip_id)
        
        if total == 0:
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"🚀 ทริป: {trip_title}\n\n⚠️ ไม่มีรายการค่าใช้จ่ายให้หาร"))
            del user_state[user_id]
            return
        
        avg = total / num_people
        msg = f"🚀 ทริป: {trip_title}\n📉 ยอดหารเฉลี่ย: {avg:,.2f} บาท/คน\n👥 จำนวนคน: {num_people:,}\n\n💵 ยอดสรุปสุทธิ (จ่ายเพิ่ม/รับคืน):\n"
        
        for uid, amt in user_totals.items():
            name = get_display_name(uid, group_id)
            diff = amt - avg
            if diff > 0:
                msg += f"• {name}: รับคืน {diff:,.2f} บาท\n"
            elif diff < 0:
                msg += f"• {name}: จ่ายเพิ่ม {abs(diff):,.2f} บาท\n"
            else:
                msg += f"• {name}: เรียบร้อยแล้ว\n"
        
        supabase.table("trips").update({"status": "closed"}).eq("id", trip_id).execute()
        del user_state[user_id]
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return

    # =================================================================
    # [อัปเดตล่าสุด 2026-05-21]: ระบบดักจับคำสั่งพิมพ์ โชว์/showtime และอัปเดตแก้ไขศิลปินพิเศษ
    # =================================================================
    if text_lower in ["โชว์", "showtime"]:
        user_state[user_id] = {"action": "waiting_showtime_image"}
        line_bot_api.reply_message(reply_token, TextSendMessage(text="📸 กรุณาส่งรูปภาพตารางแสดงดนตรีเพื่อสแกนเวลาและชื่อศิลปินครับ"))
        return

    if user_id in user_state and user_state[user_id].get("action") == "edit_showtime":
        user_state[user_id]["custom_artist"] = text
        msg = f"📋 **สรุปตารางแสดงดนตรี (อัปเดตล่าสุด)**\n\n"
        msg += f"⏱️ เวลาโชว์: {user_state[user_id].get('detected_time', 'ไม่ระบุ')}\n"
        msg += f"🎤 ศิลปิน/วง: {text} (แก้ไขเรียบร้อยแล้ว)"
        del user_state[user_id]
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return

    # เมนูหลัก
    if text_lower in ["เมนู", "menu"]:
        send_menu(reply_token)
        return

    # ยกเลิกโหมด
    if text_lower in ["ยกเลิก", "cancel"]:
        if user_id in user_state:
            del user_state[user_id]
            line_bot_api.reply_message(reply_token, TextSendMessage(text="✅ ยกเลิกโหมดเรียบร้อย"))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="ℹ️ ไม่มีโหมดที่กำลังทำงานอยู่"))
        return

    # จัดการแก้ไขยอด (edit)
    if text_lower.startswith("edit"):
        trip = get_active_trip(user_id, group_id)
        if not trip:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่มีทริปที่กำลังทำงานอยู่"))
            return
        
        expenses = get_all_expenses(trip['id'])
        if not expenses:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่มีรายการค่าใช้จ่ายให้แก้ไข"))
            return
        
        parts = text.split()
        if len(parts) >= 3:
            try:
                seq_id = int(parts[1])
                new_amount = float(parts[2].replace(',', ''))
                selected = next((e for e in expenses if e['seq_id'] == seq_id), None)
                if selected and new_amount > 0:
                    if update_expense_amount(selected['id'], new_amount):
                        line_bot_api.reply_message(reply_token, TextSendMessage(
                            text=f"✅ แก้ไขรายการลำดับที่ {seq_id} ({selected['item_name'][:30]}) จาก {selected['amount']:,.2f} บาท เป็น {new_amount:,.2f} บาท เรียบร้อย!"
                        ))
                    else:
                        line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ แก้ไขไม่สำเร็จ"))
                    if user_id in user_state:
                        del user_state[user_id]
                    return
            except:
                pass
        
        if user_id in user_state:
            del user_state[user_id]
        
        msg = "✏️ เลือกรายการที่ต้องการแก้ไขยอดเงิน (พิมพ์ลำดับที่):\n=======================\n"
        for exp in expenses:
            short_name = exp['item_name'][:35] if len(exp['item_name']) > 35 else exp['item_name']
            msg += f"ลำดับที่ {exp['seq_id']}. {short_name}\n   💰 {exp['amount']:,.2f} บาท\n"
        
        msg += "\n=======================\n"
        msg += "👉 พิมพ์ 'edit 2 500' เพื่อเปลี่ยนลำดับที่ 2 เป็น 500 บาท\n"
        msg += "👉 พิมพ์ 'ยกเลิก' หรือ 'cancel' เพื่อออก"
        
        user_state[user_id] = {"action": "edit_selection", "expenses": expenses}
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return
    
    if user_id in user_state and user_state[user_id].get("action") == "edit_selection":
        expenses = user_state[user_id]["expenses"]
        try:
            seq_id = int(text)
            selected = next((e for e in expenses if e['seq_id'] == seq_id), None)
            if selected:
                user_state[user_id] = {
                    "action": "edit_amount",
                    "expense_id": selected['id'],
                    "expense_seq": seq_id,
                    "expense_item": selected['item_name'],
                    "old_amount": selected['amount']
                }
                line_bot_api.reply_message(reply_token, TextSendMessage(
                    text=f"✏️ แก้ไขรายการลำดับที่ {seq_id}: {selected['item_name'][:50]}\n💰 ยอดเดิม: {selected['amount']:,.2f} บาท\n\n👉 พิมพ์จำนวนเงินใหม่ (เช่น 500)\n👉 พิมพ์ 'ยกเลิก' หรือ 'cancel' เพื่อออก"
                ))
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text=f"⚠️ ไม่พบรายการลำดับที่ {seq_id}"))
                del user_state[user_id]
        except ValueError:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาพิมพ์ลำดับที่เป็นตัวเลขเท่านั้น"))
            del user_state[user_id]
        return
    
    if user_id in user_state and user_state[user_id].get("action") == "edit_amount":
        try:
            new_amount = float(text.replace(',', ''))
            if new_amount <= 0:
                raise ValueError
            expense_id = user_state[user_id]["expense_id"]
            expense_seq = user_state[user_id]["expense_seq"]
            expense_item = user_state[user_id]["expense_item"]
            old_amount = user_state[user_id]["old_amount"]
            
            if update_expense_amount(expense_id, new_amount):
                line_bot_api.reply_message(reply_token, TextSendMessage(
                    text=f"✅ แก้ไขรายการลำดับที่ {expense_seq} ({expense_item[:30]}) จาก {old_amount:,.2f} บาท เป็น {new_amount:,.2f} บาท เรียบร้อย!"
                ))
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ แก้ไขไม่สำเร็จ"))
        except:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาพิมพ์จำนวนเงินเป็นตัวเลข (เช่น 500)"))
        del user_state[user_id]
        return

    # id
    if text_lower == "id":
        msg = f"🔑 [LINE ID Info]\n\n👤 User ID (ของคุณ):\n{user_id}"
        if group_id:
            msg += f"\n\n👥 Group ID:\n{group_id}"
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return

    # สร้างทริป (แก้ไขการตัดชื่อ)
    if text_lower in ["ทริป", "trip"]:
        line_bot_api.reply_message(reply_token, TextSendMessage(text="📝 พิมพ์ชื่อทริปที่ต้องการสร้าง เช่น 'ทริป ภูเก็ต'"))
        return
    
    create_trip_match = re.match(r'^(ทริป|trip)\s+(.+)$', text_lower, re.IGNORECASE)
    if create_trip_match:
        trip_name = create_trip_match.group(2).strip()
        if not trip_name:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาระบุชื่อทริป เช่น 'ทริป mujirock'"))
            return
        
        active_trip = get_active_trip(user_id, group_id)
        if active_trip:
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"⚠️ มีทริป '{active_trip['title']}' กำลังทำงานอยู่ กรุณาพิมพ์ 'จบทริป' เพื่อปิดทริปเดิมก่อนสร้างทริปใหม่"))
            return
        
        try:
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

    # ยอดรวม
    if text_lower in ["ยอด", "sum"]:
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

    # จบทริป
    if text_lower in ["จบทริป", "end trip"]:
        trip = get_active_trip(user_id, group_id)
        if not trip:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่มีทริปที่กำลังทำงานอยู่"))
            return
        
        user_state[user_id] = {"action": "end_trip", "trip_id": trip['id'], "trip_title": trip['title']}
        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"🏁 ปิดทริป: {trip['title']}\n\n👥 ระบุจำนวนคนที่จะหารครับ (มากกว่า 0):"))
        return

    # บันทึกค่าใช้จ่ายด้วยข้อความ
    if not text_lower.startswith(("ทริป", "trip", "ยอด", "sum", "จบทริป", "end", "id", "event", "stop", "edit", "เมนู", "menu", "ยกเลิก", "cancel")):
        trip = get_active_trip(user_id, group_id)
        if trip:
            name, item, amount = parse_expense_text(text)
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

    # event & stop event
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

    if text_lower == "stop event":
        events = get_active_events()
        if not events:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ ไม่มี Event ที่กำลังทำงานอยู่"))
            return
        user_state[user_id] = {"action": "stop_event", "events": events}
        msg = "🚫 เลือกหมายเลข Event ที่คุณต้องการสั่งหยุดทำงาน (Stop):\n=======================\n"
        for i, e in enumerate(events, 1):
            msg += f"{i}. งาน: {e.get('name', '-')}\n🛑 (ID ย่อ: {e.get('id', '-')})\n-----------------------\n"
        msg += "👉 พิมพ์เฉพาะ [ตัวเลขลำดับ] เพื่อระบุเลือกปิดงานชิ้นนั้นได้เลยครับ"
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
        return

    if user_id in user_state and user_state[user_id].get("action") == "stop_event":
        try:
            choice = int(text) - 1
            events = user_state[user_id]["events"]
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
        except:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ กรุณาพิมพ์หมายเลขเท่านั้น"))
        del user_state[user_id]
        return

# =============================================================
# รองรับรูปภาพสลิป/บิล
# =============================================================
def process_slip(message_id, trip_id, user_id, group_id, reply_token):
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
            
            new_id = result.data[0]['id'] if result.data else None
            expenses = get_all_expenses(trip_id)
            seq_id = len(expenses)
            
            success_msg = f"✅ บันทึกจำนวนเงิน {amount:,.2f} บาท จากคุณ {sender_name} สำเร็จ!"
            if seq_id:
                success_msg += f"\n\n✏️ หากยอดไม่ถูกต้อง พิมพ์: edit {seq_id} {amount}"
            line_bot_api.reply_message(reply_token, TextSendMessage(text=success_msg))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(
                text="⚠️ ไม่พบจำนวนเงินในรูป หรือไม่ใช่สลิปการเงิน\n\n📌 ลองบันทึกด้วยข้อความ เช่น 'บอล ค่าเหล้า 500'\n✏️ หรือพิมพ์ 'edit' เพื่อแก้ไขภายหลัง"
            ))
    except Exception as e:
        logger.error(f"Process slip error: {e}")
        line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ ไม่สามารถอ่านรูปได้ กรุณาลองใหม่"))
        
# =================================================================
# [อัปเดตล่าสุด 2026-05-21]: เพิ่มฟังก์ชันประมวลผลรูปภาพสำหรับสแกนโชว์ไทม์ (วางต่อท้ายฟังก์ชัน process_slip)
# =================================================================
def process_showtime(message_id, user_id, reply_token):
    try:
        message_content = line_bot_api.get_message_content(message_id)
        image_bytes = b''.join(message_content.iter_content())
        response = vision_client.text_detection(image=vision.Image(content=image_bytes))
        text_detected = response.text_annotations[0].description if response.text_annotations else ""
        
        time_match = re.search(r'(\d{2}[:\.]\d{2})', text_detected)
        detected_time = time_match.group(1) if time_match else "ไม่ระบุเวลา"
        
        lines = [line.strip() for line in text_detected.split('\n') if line.strip()]
        detected_artist = "ไม่พบชื่อศิลปิน"
        for i, line in enumerate(lines):
            if time_match and time_match.group(1) in line:
                if i + 1 < len(lines):
                    detected_artist = lines[i+1]
                break
                
        msg = f"📋 **Showtime ข้อมูลตารางการแสดง**\n\n"
        msg += f"⏱️ เวลาโชว์: {detected_time}\n"
        msg += f"🎤 ศิลปิน/วง: {detected_artist}\n\n"
        msg += f"✏️ หากต้องการแก้ไขเนื่องจากมีศิลปินพิเศษ สามารถพิมพ์ชื่อศิลปินใหม่ส่งมาได้เลยครับ"
        
        user_state[user_id] = {
            "action": "edit_showtime",
            "detected_time": detected_time,
            "detected_artist": detected_artist
        }
        line_bot_api.reply_message(reply_token, TextSendMessage(text=msg))
    except Exception as e:
        logger.error(f"Process showtime error: {e}")
        line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ ระบบไม่สามารถอ่านข้อมูลโชว์ไทม์ได้ กรุณาลองใหม่อีกครั้ง"))
        
@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    user_id = event.source.user_id
    group_id = getattr(event.source, 'group_id', None)
    
    # =================================================================
    # [อัปเดตล่าสุด 2026-05-21]: ตรวจสอบเงื่อนไขสถานะสเตตัสการส่งรูปภาพของโชว์ไทม์
    # =================================================================
    if user_id in user_state and user_state[user_id].get("action") == "waiting_showtime_image":
        threading.Thread(target=process_showtime, args=(event.message.id, user_id, event.reply_token)).start()
        return

    # [Comment เดิม] เช็คทริปสำหรับส่งสลิปค่าใช้จ่ายปกติ
    trip = get_active_trip(user_id, group_id)
    if not trip:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ ไม่มีทริปที่กำลังทำงานอยู่ พิมพ์ 'ทริป ชื่อทริป' เพื่อเริ่มทริป"))
        return
    threading.Thread(target=process_slip, args=(event.message.id, trip['id'], user_id, group_id, event.reply_token)).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5177))
    app.run(host="0.0.0.0", port=port, debug=True)