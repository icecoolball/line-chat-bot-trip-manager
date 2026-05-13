import os
import re
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, ImageMessage, TextSendMessage

app = Flask(__name__)

# ใช้ค่าจาก Environment Variables
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except Exception:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    # รับรูปภาพ
    message_content = line_bot_api.get_message_content(event.message.id)
    
    # ตรงนี้คือจุดที่เราจะใส่ Google Vision API ในอนาคต
    # ตอนนี้ให้บอทตอบกลับก่อนว่าได้รับรูปแล้ว
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text="ได้รับสลิปแล้ว! ระบบกำลังเชื่อมต่อกับ Google Vision API เพื่อดึงยอดเงิน...")
    )

if __name__ == "__main__":
    app.run()