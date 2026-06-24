"""สร้างไอคอนสีสำหรับปุ่ม Quick Reply (วงกลมสี + สัญลักษณ์เส้นสีขาว) แล้วอัปโหลดขึ้น Supabase public.

ไม่พึ่ง Pillow — เขียน PNG ด้วย zlib ล้วน (stdlib) เพื่อให้รันได้ทุกที่.
ใช้:  python scripts/setup_qr_icons.py   (อ่าน SUPABASE_URL/SUPABASE_KEY จาก env หรือ .env)
อัปไป bucket "trip-exports" path qr/<key>.png ให้ตรงกับ QR_ICON_BASE ใน src/worker.ts
"""
import os
import struct
import zlib

from supabase import create_client

SIZE = 96
PURPLE = (124, 58, 237)
GREEN = (34, 153, 84)
RED = (211, 47, 47)
GRAY = (90, 90, 90)
BLUE = (37, 99, 175)
TEAL = (15, 110, 86)
WHITE = (255, 255, 255, 255)


class Canvas:
    def __init__(self, size):
        self.s = size
        self.buf = bytearray(size * size * 4)

    def px(self, x, y, rgba):
        if 0 <= x < self.s and 0 <= y < self.s:
            i = (y * self.s + x) * 4
            self.buf[i:i + 4] = bytes(rgba)

    def circle(self, cx, cy, r, rgba):
        for y in range(cy - r, cy + r + 1):
            for x in range(cx - r, cx + r + 1):
                if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                    self.px(x, y, rgba)

    def disk_color(self, rgb):
        self.circle(self.s // 2, self.s // 2, self.s // 2 - 3, (rgb[0], rgb[1], rgb[2], 255))

    def thick_line(self, x0, y0, x1, y1, w, rgba):
        steps = max(abs(x1 - x0), abs(y1 - y0), 1)
        for t in range(steps + 1):
            x = round(x0 + (x1 - x0) * t / steps)
            y = round(y0 + (y1 - y0) * t / steps)
            for dy in range(-w, w + 1):
                for dx in range(-w, w + 1):
                    if dx * dx + dy * dy <= w * w:
                        self.px(x + dx, y + dy, rgba)

    def hbar(self, x0, x1, y, w, rgba):
        self.thick_line(x0, y, x1, y, w, rgba)

    def png(self):
        rows = bytearray()
        for y in range(self.s):
            rows.append(0)
            rows.extend(self.buf[y * self.s * 4:(y + 1) * self.s * 4])
        comp = zlib.compress(bytes(rows), 9)

        def chunk(tag, data):
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

        ihdr = struct.pack(">IIBBBBB", self.s, self.s, 8, 6, 0, 0, 0)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", comp) + chunk(b"IEND", b"")


def glyph_check(c):
    c.thick_line(30, 50, 44, 64, 4, WHITE)
    c.thick_line(44, 64, 70, 32, 4, WHITE)


def glyph_cross(c):
    c.thick_line(32, 32, 64, 64, 4, WHITE)
    c.thick_line(64, 32, 32, 64, 4, WHITE)


def glyph_plus(c):
    c.thick_line(48, 30, 48, 66, 4, WHITE)
    c.thick_line(30, 48, 66, 48, 4, WHITE)


def glyph_bars(c):
    for y in (38, 48, 58):
        c.hbar(32, 64, y, 3, WHITE)


def glyph_info(c):
    c.circle(48, 33, 4, WHITE)
    c.thick_line(48, 44, 48, 66, 4, WHITE)


def glyph_calendar(c):
    c.thick_line(32, 36, 64, 36, 3, WHITE)  # top
    c.thick_line(32, 64, 64, 64, 3, WHITE)  # bottom
    c.thick_line(32, 36, 32, 64, 3, WHITE)
    c.thick_line(64, 36, 64, 64, 3, WHITE)
    c.hbar(32, 64, 44, 2, WHITE)


def glyph_dot(c):
    c.circle(48, 48, 10, WHITE)


# key: (สีวงกลม, ฟังก์ชันวาดสัญลักษณ์)
ICONS = {
    "today": (PURPLE, glyph_calendar),
    "sum": (TEAL, glyph_dot),
    "history": (GRAY, glyph_bars),
    "end": (RED, glyph_check),
    "add": (GREEN, glyph_plus),
    "help": (BLUE, glyph_info),
    "cancel": (RED, glyph_cross),
    "yes": (GREEN, glyph_check),
    "th": (RED, glyph_dot),
    "jp": (RED, glyph_dot),
    "kr": (BLUE, glyph_dot),
    "cn": (RED, glyph_dot),
}


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


def load_key():
    for name in ("SUPABASE_KEY", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY"):
        try:
            return load_env(name)
        except RuntimeError:
            continue
    raise RuntimeError("ไม่พบ SUPABASE key ใด ๆ")


def main():
    supabase = create_client(load_env("SUPABASE_URL"), load_key())
    storage = supabase.storage.from_("trip-exports")
    for key, (color, glyph) in ICONS.items():
        c = Canvas(SIZE)
        c.disk_color(color)
        glyph(c)
        png = c.png()
        path = f"qr/{key}.png"
        storage.upload(path=path, file=png, file_options={"content-type": "image/png", "upsert": "true"})
        print("อัปโหลด:", storage.get_public_url(path))
    print("เสร็จ — ไอคอนพร้อมใช้ใน Quick Reply")


if __name__ == "__main__":
    main()
