"""ตั้งค่า LINE Rich Menu (เมนูล่างถาวร) ให้บอต Trip Manager — รันครั้งเดียว.

ใช้:  python scripts/setup_richmenu.py
อ่าน LINE_CHANNEL_ACCESS_TOKEN จาก environment หรือไฟล์ .env

สร้างรูปเมนู 2500x843 แบ่ง 4 ปุ่ม (ยอดวันนี้ / ยอดรวม / ประวัติ / จบทริป)
แล้วอัปโหลด + ตั้งเป็นเมนูเริ่มต้นของทุกคน
"""
import io
import os
import sys

import requests
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 2500, 843
ACCENT = (124, 58, 237)        # #7C3AED
ACCENT_DARK = (90, 40, 180)
WHITE = (255, 255, 255)

# ปุ่ม: (ข้อความบนรูป, คำสั่งที่ส่งเมื่อกด)
CELLS = [
    ("ยอดวันนี้", "ยอดวันนี้"),
    ("ยอดรวม", "ยอด"),
    ("ประวัติ", "history"),
    ("จบทริป", "จบทริป"),
]

THAI_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\leelawui.ttf",   # Leelawadee UI (Windows, รองรับไทย)
    r"C:\Windows\Fonts\tahoma.ttf",     # Tahoma (Windows, รองรับไทย)
    r"C:\Windows\Fonts\THSarabunNew.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansThai-Regular.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
]


def load_env_token():
    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
    if token:
        return token
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("LINE_CHANNEL_ACCESS_TOKEN="):
                    return line.split("=", 1)[1].strip().strip('"')
    raise RuntimeError("ไม่พบ LINE_CHANNEL_ACCESS_TOKEN (ตั้งใน env หรือ .env)")


def pick_font(size):
    for path in THAI_FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size), True
            except Exception:
                continue
    return ImageFont.load_default(), False


def build_image():
    img = Image.new("RGB", (WIDTH, HEIGHT), ACCENT)
    draw = ImageDraw.Draw(img)
    font, thai_ok = pick_font(110)
    if not thai_ok:
        print("เตือน: ไม่พบฟอนต์ไทย รูปอาจแสดงข้อความไม่ครบ (ปุ่มยังกดได้ปกติ)", file=sys.stderr)
    cell_w = WIDTH // len(CELLS)
    for i, (label, _) in enumerate(CELLS):
        x0 = i * cell_w
        if i % 2 == 1:
            draw.rectangle([x0, 0, x0 + cell_w, HEIGHT], fill=ACCENT_DARK)
        if i > 0:
            draw.line([(x0, 80), (x0, HEIGHT - 80)], fill=WHITE, width=3)
        box = draw.textbbox((0, 0), label, font=font)
        tw, th = box[2] - box[0], box[3] - box[1]
        draw.text((x0 + (cell_w - tw) / 2 - box[0], (HEIGHT - th) / 2 - box[1]), label, font=font, fill=WHITE)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def main():
    token = load_env_token()
    headers = {"Authorization": f"Bearer {token}"}
    cell_w = WIDTH // len(CELLS)
    body = {
        "size": {"width": WIDTH, "height": HEIGHT},
        "selected": True,
        "name": "Trip Manager Menu",
        "chatBarText": "เมนู",
        "areas": [
            {
                "bounds": {"x": i * cell_w, "y": 0, "width": cell_w, "height": HEIGHT},
                "action": {"type": "message", "text": cmd},
            }
            for i, (_, cmd) in enumerate(CELLS)
        ],
    }

    res = requests.post("https://api.line.me/v2/bot/richmenu", headers={**headers, "Content-Type": "application/json"}, json=body, timeout=20)
    res.raise_for_status()
    rich_menu_id = res.json()["richMenuId"]
    print("สร้าง rich menu:", rich_menu_id)

    img_bytes = build_image()
    res = requests.post(
        f"https://api-data.line.me/v2/bot/richmenu/{rich_menu_id}/content",
        headers={**headers, "Content-Type": "image/png"},
        data=img_bytes, timeout=30,
    )
    res.raise_for_status()
    print("อัปโหลดรูปแล้ว")

    res = requests.post(f"https://api.line.me/v2/bot/user/all/richmenu/{rich_menu_id}", headers=headers, timeout=20)
    res.raise_for_status()
    print("ตั้งเป็นเมนูเริ่มต้นแล้ว — เปิดแชทใหม่จะเห็นเมนูล่าง")


if __name__ == "__main__":
    main()
