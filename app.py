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

# คง Comment เดิม: เพิ่ม logging เพื่อให้ debug ได้ง่ายขึ้น ดูใน Railway/Render logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- 1. การตั้งค่าเริ่มต้น ---\nline_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))

# Google Vision
creds_dict = json.loads(os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON'))
creds = service_account.Credentials.from_service_account_info(creds_dict)
vision_client = vision.ImageAnnotatorClient(credentials=creds)

supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_ANON_KEY"))

user_state = {}
SCHEDULES_FILE = "schedules.local.json"

# =================================================================
# [คง Comment เดิม]: ฟังก์ชันโหลดและบันทึก schedules
# =================================================================
def load_schedules_from_file():
    if os.path.exists(SCHEDULES_FILE):
        try:
            with open(SCHEDULES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Load schedules error: {e}")
    return []

def save_schedules_to_file(schedules):
    try:
        with open(SCHEDULES_FILE, "w", encoding="utf-8") as f:
            json.dump(schedules, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Save schedules error: {e}")

# =================================================================
# [อัปเดตล่าสุด 2026-05-21]: ปรับฟังก์ชันหาและจัดการทริปให้ผูกกับตู้เก็บกลุ่ม (Group/Room) หรือแยกใช้ User ID กรณีคุยเดี่ยว
# เพื่อให้ทุกคนในกลุ่มกดใช้คำสั่ง สร้าง/บันทึก/แก้ไข/ปิดทริป ร่วมกันในตารางข้อมูลเดียวกันได้ทั้งหมด
# =================================================================
def get_context_id(event):
    if event.source.type == 'group':
        return event.source.group_id
    elif event.source.type == 'room':
        return event.source.room_id
    return event.source.user_id

def get_active_trip(context_id):
    try:
        res = supabase.table("trips").select("*").eq("user_id", context_id).eq("is_active", True).execute()
        if res.data and len(res.data) > 0:
            return res.data[0]
    except Exception as e:
        logger.error(f"Get active trip error: {e}")
    return None

def create_trip(context_id, trip_name):
    try:
        supabase.table("trips").update({"is_active": False}).eq("user_id", context_id).eq("is_active", True).execute()
        res = supabase.table("trips").insert({"user_id": context_id, "trip_name": trip_name, "is_active": True}).execute()
        if res.data:
            return res.data[0]
    except Exception as e:
        logger.error(f"Create trip error: {e}")
    return None

def end_trip_db(trip_id):
    try:
        supabase.table("trips").update({"is_active": False}).eq("id", trip_id).execute()
        return True
    except Exception as e:
        logger.error(f"End trip error: {e}")
        return False

# =================================================================
# [อัปเดตล่าสุด 2026-05-21]: ปรับการดึงข้อมูลรายจ่ายและแก้ยอดเงินให้ฟิลเตอร์เงื่อนไข "เฉพาะทริปที่ยังทำงานอยู่ปัจจุบันเท่านั้น"
# จะไม่ดึงยอดเก่าที่ end trip ไปแล้วขึ้นมาแสดงผลซ้ำซาก
# =================================================================
def add_expense(trip_id, sender_name, amount, details=""):
    try:
        res = supabase.table("expenses").insert({
            "trip_id": trip_id,
            "sender_name": sender_name,
            "amount": amount,
            "details": details
        }).execute()
        if res.data:
            return res.data[0].get("id")
    except Exception as e:
        logger.error(f"Add expense error: {e}")
    return None

def get_trip_expenses(trip_id):
    try:
        res = supabase.table("expenses").select("*").eq("trip_id", trip_id).order("id", desc=False).execute()
        return res.data or []
    except Exception as e:
        logger.error(f"Get trip expenses error: {e}")
        return []

def update_expense_amount(expense_id, trip_id, new_amount):
    try:
        res = supabase.table("expenses").update({"amount": new_amount}).eq("id", expense_id).eq("trip_id", trip_id).execute()
        return len(res.data) > 0 if res.data else False
    except Exception as e:
        logger.error(f"Update expense error: {e}")
        return False

# =================================================================
# [อัปเดตล่าสุด 2026-05-21]: ปรับปรุงลอจิก OCR ตรวจจับสลิป ป้องกันการดึงตัวเลขมั่วซั่ว
# คัดกรองตัวเลขด้วย Regex ค้นหาคีย์เวิร์ด 'บาท' / 'baht' หรือสแกนหาข้อความจำนวนเงินโอนที่มีทศนิยม .00 เด่นชัด
# =================================================================
def clean_amount_string(text_str):
    text_str = text_str.replace(",", "").strip()
    match = re.search(r"(\d+\.\d{2})", text_str)
    if match:
        return float(match.group(1))
    if text_str.isdigit():
        return float(text_str)
    return None

def detect_slip_amount(image_content):
    try:
        image = vision.Image(content=image_content)
        response = vision_client.text_detection(image=image)
        texts = response.text_annotations
        if not texts:
            return None
        
        full_text = texts[0].description
        lines = full_text.split("\n")
        logger.info(f"OCR Full Text Lines: {lines}")

        for i, line in enumerate(lines):
            line_lower = line.lower()
            if "บาท" in line_lower or "baht" in line_lower:
                cleaned = clean_amount_string(line)
                if cleaned and cleaned > 0:
                    return cleaned
                if i > 0:
                    cleaned_prev = clean_amount_string(lines[i-1])
                    if cleaned_prev and cleaned_prev > 0:
                        return cleaned_prev

        for line in lines:
            if "." in line:
                cleaned = clean_amount_string(line)
                if cleaned and cleaned > 0:
                    return cleaned
                    
    except Exception as e:
        logger.error(f"OCR detection exception: {e}")
    return None

# --- 2. Flask Routes ---
@app.route("/callback", models=["POST"])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    logger.info(f"Request body: {body}")
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@app.route('/')
def home():
    return "Ticket Prep Backend is Running!"

@app.route('/api/server-time', methods=['GET'])
def server_time():
    now_ms = int(datetime.utcnow().timestamp() * 1000)
    return jsonify({"ok": True, "serverTime": now_ms})

@app.route('/api/schedules', methods=['GET'])
def get_schedules():
    return jsonify(load_schedules_from_file())

# =================================================================
# [อัปเดตล่าสุด 2026-05-21]: ปรับแต่งลอจิกประมวลผลคำสั่งทางข้อความตัวอักษร
# 1. รองรับการแปลงพิมพ์เล็ก/พิมพ์ใหญ่ภาษาอังกฤษผ่านวิธีการครอบสตรีมคำสั่งด้วย .strip().lower()
# 2. แก้ไขระบบเก็บข้อมูลทริปรายบุคคลเป็นผูกตามรหัสกลุ่มแชท ทำให้ทุกคนคุยหารเงินแก้รายการทริปเดียวกันได้
# 3. แก้ไขคำสั่ง edit ทับซ้อน ยินยอมให้พิมพ์แก้แบบเว้นวรรคปกติ 'edit 0001 250' โดยไม่ต้องพึ่งตัวสัญลักษณ์ '>'
# 4. ปรับแก้ไข State ในตัวแปร 'end_trip_waiting' ยินยอมให้กรอกเลขจำนวนผู้หารมากกว่า 1 ได้สำเร็จไม่มีเออร์เรอร์ตกค้าง
# =================================================================
@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    raw_text = event.message.text.strip()
    msg_clean = raw_text.lower()
    context_id = get_context_id(event)
    sender_id = event.source.user_id
    
    try:
        profile = line_bot_api.get_profile(sender_id)
        sender_name = profile.display_name
    except Exception:
        sender_name = "ผู้ใช้งาน"

    # ตรวจสอบ State การรอรับจำนวนคนหารเงินหลัง end trip
    if context_id in user_state and user_state[context_id].get("state") == "end_trip_waiting":
        if raw_text.isdigit():
            people_count = int(raw_text)
            if people_count >= 1:
                trip_info = user_state[context_id]["trip"]
                expenses = get_trip_expenses(trip_info["id"])
                total_amount = sum(exp["amount"] for exp in expenses)
                per_person = total_amount / people_count
                
                summary = f"🏁 ปิดทริป: {trip_info['trip_name']}\n💰 ยอดรวมทั้งหมด: {total_amount:,.2f} บาท\n👥 จำนวนคนหาร: {people_count} คน\n💵 ตกคนละ: {per_person:,.2f} บาท\n\n📊 รายละเอียดรายการ:"
                for exp in expenses:
                    summary += f"\n- [{exp['id']:04d}] {exp['sender_name']}: {exp['amount']:,.2f} บาท ({exp['details'] or 'ไม่ระบุ'})"
                
                end_trip_db(trip_info["id"])
                del user_state[context_id]
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=summary))
                return
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ จำนวนคนหารต้องมีอย่างน้อย 1 คนขึ้นไปครับ กรุณากรอกใหม่อีกครั้ง:"))
                return
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ กรุณากรอกระบุจำนวนคนหารเป็นตัวเลขล้วนเท่านั้นครับ:"))
            return

    # ตรวจสอบ State การตั้งชื่อทริปหลังกดปุ่มเมนู
    if context_id in user_state and user_state[context_id].get("state") == "create_trip_waiting":
        trip = create_trip(context_id, raw_text)
        if trip:
            del user_state[context_id]
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🚀 เริ่มต้นทริปใหม่: '{raw_text}' เรียบร้อยแล้ว!\n📌 สมาชิกกลุ่มทุกคนสามารถส่งสลิปหรือพิมพ์บันทึกค่าใช้จ่ายเข้ามาในทริปนี้ได้ทันที"))
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ เกิดข้อผิดพลาดในระบบ ไม่สามารถสร้างทริปได้ในขณะนี้"))
        return

    # 1. แสดงรหัสห้องแชท (ID / Group ID) รองรับพิมพ์เล็กใหญ่ทั้งหมด
    if msg_clean == "id":
        display_id = context_id
        if event.source.type == 'group':
            display_id = f"Group ID: {event.source.group_id}"
        elif event.source.type == 'room':
            display_id = f"Room ID: {event.source.room_id}"
        else:
            display_id = f"User ID: {event.source.user_id}"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"📋 รหัสการเชื่อมต่อของคุณคือ:\n`{display_id}`"))
        return

    # 2. เมนูคำสั่งแสดงผล Quick Reply รองรับพิมพ์เล็กใหญ่ทั้งหมด ปรับปุ่มให้ส่งข้อความพิมพ์เล็กตรงกับเงื่อนไขบอต
    if msg_clean == "menu":
        quick_reply_buttons = [
            QuickReplyButton(action=MessageAction(label="🌟 เริ่มทริป", text="ทริป")),
            QuickReplyButton(action=MessageAction(label="📊 แก้ไขยอดเงิน", text="edit")),
            QuickReplyButton(action=MessageAction(label="🏁 สรุปปิดทริป", text="end trip"))
        ]
        line_reply = TextSendMessage(
            text="📱 แผงควบคุมรายการหารเงินทริป\nกรุณาเลือกกดปุ่มคำสั่งที่ต้องการทำรายการด้านล่างนี้ได้เลยครับ:",
            quick_reply=QuickReply(items=quick_reply_buttons)
        )
        line_bot_api.reply_message(event.reply_token, line_reply)
        return

    # ตรวจสอบสิทธิ์การเริ่มสร้างทริปใหม่ในกลุ่มแชท
    if msg_clean == "ทริป" or msg_clean.startswith("ทริป "):
        if msg_clean == "ทริป":
            user_state[context_id] = {"state": "create_trip_waiting"}
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📝 กรุณาพิมพ์ระบุชื่อทริปที่คุณต้องการเปิดใหม่เข้ามาได้เลยครับ เช่น 'ทริปพัทยา' :"))
        else:
            trip_name = raw_text[4:].strip()
            trip = create_trip(context_id, trip_name)
            if trip:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🚀 เริ่มต้นทริปใหม่: '{trip_name}' เรียบร้อยแล้ว!\n📌 สมาชิกกลุ่มทุกคนสามารถส่งสลิปหรือพิมพ์บันทึกค่าใช้จ่ายเข้ามาในทริปนี้ได้ทันที"))
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ เกิดข้อผิดพลาดในระบบ ไม่สามารถสร้างทริปได้ในขณะนี้"))
        return

    # ตรวจสอบสิทธิ์สรุปปิดทริปการเงินภายในห้องแชท
    if msg_clean == "end trip":
        trip = get_active_trip(context_id)
        if not trip:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ ไม่พบทริปที่ยังเปิดทำงานอยู่ภายในห้องแชทนี้ พิมพ์ 'ทริป [ชื่อทริป]' เพื่อเริ่มต้น"))
            return
        
        user_state[context_id] = {"state": "end_trip_waiting", "trip": trip}
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🏁 คุณกำลังจะปิดทริป: '{trip['trip_name']}'\n👥 กรุณาพิมพ์ระบุจำนวนคนหารเงินทั้งหมดเข้ามาสิครับ (เช่น 3) :"))
        return

    # โหมดคำสั่งแก้ไขยอดเงิน รองรับทั้งพิมพ์ย่อแบบเว้นวรรคปกติ 'edit 0001 500' และโหมดโชว์รายการทั้งหมดในทริปปัจจุบัน
    if msg_clean == "edit" or msg_clean.startswith("edit "):
        trip = get_active_trip(context_id)
        if not trip:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ ไม่พบทริปที่ยังเปิดทำงานอยู่ในตอนนี้ พิมพ์ 'ทริป [ชื่อทริป]' เพื่อเริ่มต้นทริปก่อนครับ"))
            return

        # ตรวจสอบการพิมพ์ระบุอาร์กิวเมนต์ตัวเลขเพื่อแก้ยอดทันที (เช่น edit 0001 500 หรือ edit 1 500)
        tokens = re.split(r'\s+', raw_text)
        if len(tokens) >= 3:
            id_str = tokens[1]
            amount_str = tokens[2]
            
            if id_str.isdigit() and amount_str.isdigit():
                target_id = int(id_str)
                new_val = float(amount_str)
                
                if update_expense_amount(target_id, trip["id"], new_val):
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✏️ แก้ไขรายการเลขที่ [{target_id:04d}] ในทริปปัจจุบันเป็นยอดเงิน {new_val:,.2f} บาท เรียบร้อยแล้วครับ!"))
                else:
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ ไม่พบรายการเลขที่ [{target_id:04d}] ที่เป็นของทริปปัจจุบันในระบบ หรือคุณพิมพ์ข้อมูลไม่ถูกต้อง"))
                return

        # กรณีพิมพ์ edit ลอยๆ ให้แสดงเฉพาะรายการที่ลงบันทึกในทริปปัจจุบันเท่านั้น
        expenses = get_trip_expenses(trip["id"])
        if not expenses:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"📊 ทริปปัจจุบัน '{trip['trip_name']}' ยังไม่มีการลงบันทึกค่าใช้จ่ายเข้ามาในระบบเลยครับ"))
            return
            
        list_msg = f"📝 รายการค่าใช้จ่ายในทริปปัจจุบัน: '{trip['trip_name']}'\n💡 พิมพ์คำสั่ง 'edit [เลขรายการ] [ยอดใหม่]' เพื่อแก้ไขข้อมูลได้ทันที"
        for exp in expenses:
            list_msg += f"\n\n🆔 รายการ ID: {exp['id']:04d}\n👤 ผู้จ่าย: {exp['sender_name']}\n💰 ยอดเงิน: {exp['amount']:,.2f} บาท\n🏷️ บันทึก: {exp['details'] or 'ไม่ระบุ'}"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=list_msg))
        return

    # ลอจิกพิมพ์ข้อความบันทึกยอดเงินตรงๆ โดยไม่สแกนรูปภาพสลิป (เช่น บอล ค่าเหล้า 500)
    match_msg = re.match(r"^([^\d]+)\s+([^\d]+)\s+(\d+)$", raw_text)
    if match_msg:
        trip = get_active_trip(context_id)
        if not trip:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ ไม่มีทริปที่กำลังทำงานอยู่ พิมพ์ 'ทริป ชื่อทริป' เพื่อเริ่มทริปก่อนลงบันทึกเงิน"))
            return
        
        p_name = match_msg.group(1).strip()
        p_detail = match_msg.group(2).strip()
        p_amount = float(match_msg.group(3))
        
        new_id = add_expense(trip["id"], p_name, p_amount, p_detail)
        success_msg = f"✅ บันทึกค่าใช้จ่ายสำเร็จ!\n🎯 ทริป: {trip['trip_name']}\n👤 ผู้จ่าย: {p_name}\n🏷️ รายการ: {p_detail}\n💵 ยอดเงิน: {p_amount:,.2f} บาท"
        if new_id:
            success_msg += f"\n\n✏️ หากยอดไม่ถูกต้อง พิมพ์แก้ไข: edit {new_id:04d} {int(p_amount)}"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=success_msg))
        return

# =================================================================
# [อัปเดตล่าสุด 2026-05-21]: ปรับฟังก์ชันการรับและดึงค่าจากไฟล์รูปภาพสลิปธนาคาร
# เปลี่ยนลอจิกคลังเก็บจากผูก User ID รายคนไปล็อกผูกเข้ากับ ID กลุ่มแชท เพื่อแชร์ข้อมูลสลิปร่วมกันในทริปเดียวของกลุ่มทั้งหมด
# =================================================================
@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    context_id = get_context_id(event)
    trip = get_active_trip(context_id)
    if not trip:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ ไม่มีทริปที่กำลังทำงานอยู่ภายในห้องแชทนี้ พิมพ์ 'ทริป ชื่อทริป' เพื่อเริ่มทริปก่อนส่งสลิปครับ"))
        return
        
    sender_id = event.source.user_id
    try:
        profile = line_bot_api.get_profile(sender_id)
        sender_name = profile.display_name
    except Exception:
        sender_name = "ผู้ใช้งานในกลุ่ม"
        
    threading.Thread(target=process_slip_async, args=(event.message.id, event.reply_token, trip, sender_name)).start()

def process_slip_async(message_id, reply_token, trip, sender_name):
    try:
        message_content = line_bot_api.get_message_content(message_id)
        image_bytes = b""
        for chunk in message_content.iter_content():
            image_bytes += chunk
            
        amount = detect_slip_amount(image_bytes)
        if amount and amount > 0:
            new_id = add_expense(trip["id"], sender_name, amount, "สลิปโอนเงิน")
            success_msg = f"✅ บันทึกจากรูปสลิปสำเร็จ!\n🎯 ทริปปัจจุบัน: {trip['trip_name']}\n👤 ผู้จ่าย: {sender_name}\n💵 ยอดเงินตรวจเจอ: {amount:,.2f} บาท"
            
            if new_id:
                id_display = f"{new_id:04d}"
                success_msg += f"\\n\\n✏️ หากยอดไม่ถูกต้อง พิมพ์แก้ไข: edit {id_display} {int(amount)}"
            else:
                success_msg += f"\\n\\n✏️ หากยอดไม่ถูกต้อง พิมพ์ 'edit' เพื่อดูรายการและแก้ไขภายหลัง"
                
            line_bot_api.reply_message(reply_token, TextSendMessage(text=success_msg))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(
                text="⚠️ ตรวจสอบภาพสลิปแล้วไม่พบตัวเลขยอดโอนเงิน หรือไม่ใช่สลิปทำรายการการเงิน\\n\\n"
                     "📌 แนะนำให้พิมพ์บันทึกด้วยข้อความแทน เช่น 'บอล ค่าเหล้า 500'\\n"
                     "✏️ หรือพิมพ์คำสั่ง 'edit' เพื่อเข้าไปแก้ไขภายหลังได้ครับ"
            ))
    except Exception as e:
        logger.error(f"Process slip error: {e}")
        line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ ระบบไม่สามารถอ่านรูปภาพนี้ได้ กรุณาลองส่งใหม่อีกครั้งครับ"))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)