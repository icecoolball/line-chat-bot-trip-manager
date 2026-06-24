"""สร้างไอคอนสีสำหรับปุ่ม Quick Reply แล้วอัปโหลดขึ้น Supabase (public) — รันครั้งเดียว.

ใช้:  python scripts/setup_qr_icons.py
อ่าน SUPABASE_URL / SUPABASE_KEY จาก environment หรือไฟล์ .env

อัปโหลดไป bucket "trip-exports" path qr/<key>.png ให้ URL ตรงกับ QR_ICON_BASE ใน src/worker.ts
ไอคอน = วงกลมสี + สัญลักษณ์/ตัวอักษรสีขาว (เด่นทุกธีมแชท)
"""
import io
import os

from PIL import Image, ImageDraw, ImageFont
from supabase import create_client

SIZE = 120
PURPLE = (124, 58, 237)
GREEN = (34, 153, 84)
RED = (211, 47, 47)
GRAY = (90, 90, 90)
BLUE = (37, 99, 175)

# key: (สี, ข้อความบนไอคอน, ขนาดฟอนต์)
ICONS = {
    "today": (PURPLE, "฿", 70),
    "sum": (PURPLE, "Σ", 66),
    "history": (GRAY, "≡", 70),
    "end": (RED, "✓", 64),
    "add": (GREEN, "+", 80),
    "help": (BLUE, "?", 70),
    "cancel": (RED, "✕", 60),
    "yes": (GREEN, "✓", 64),
    "th": (RED, "TH", 44),
    "jp": (RED, "JP", 44),
    "kr": (BLUE, "KR", 44),
    "cn": (RED, "CN", 44),
}

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\tahomabd.ttf",
    r"C:\Windows\Fonts\seguisym.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def load_env(name):
    val = os.getenv(name)
    if val:
        return val
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(f"{name}="):
                    return line.split("=", 1)[1].strip().strip('"')
    raise RuntimeError(f"ไม่พบ {name}")


def pick_font(size):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def make_icon(color, text, font_size):
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([4, 4, SIZE - 4, SIZE - 4], fill=color + (255,))
    font = pick_font(font_size)
    box = d.textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    d.text(((SIZE - tw) / 2 - box[0], (SIZE - th) / 2 - box[1]), text, font=font, fill=(255, 255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def main():
    supabase = create_client(load_env("SUPABASE_URL"), load_env("SUPABASE_KEY"))
    storage = supabase.storage.from_("trip-exports")
    for key, (color, text, fsize) in ICONS.items():
        png = make_icon(color, text, fsize)
        path = f"qr/{key}.png"
        storage.upload(path=path, file=png, file_options={"content-type": "image/png", "upsert": "true"})
        print("อัปโหลด:", storage.get_public_url(path))
    print("เสร็จ — ไอคอนพร้อมใช้ใน Quick Reply")


if __name__ == "__main__":
    main()
