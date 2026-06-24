# Handoff — line-trip-bot (Trip Manager)

## อัปเดตความปลอดภัย 2026-06-24

- ลบข้อมูลทริปทดสอบเดิมแล้ว (`trips`, `expenses`, `daily_summaries`, `export_jobs` เหลือ 0)
- `fx_rates` เปิด RLS และให้เข้าถึงผ่าน `SUPABASE_SERVICE_ROLE_KEY` เท่านั้น
- เรทสำรองมี source of truth เดียวที่ `config/fallback-rates.json`; Worker และ Python export อ่านไฟล์เดียวกัน
- debt เรื่องข้อมูล payer ของทริปเก่าและ fallback rates ซ้ำปิดแล้ว
- ตรวจด้วย `npm test`, `npm run typecheck` และ `python -m unittest test/test_fallback_rates.py`

อัปเดต: 2026-06-24

LINE bot สำหรับจดค่าใช้จ่ายทริปในกลุ่ม → หารเงิน, หลายสกุลเงิน (แปลงบาทเรทสด), export Excel, สรุปรายวันอัตโนมัติ

---

## สถาปัตยกรรม

| ส่วน | ที่อยู่ | หน้าที่ |
|---|---|---|
| **Cloudflare Worker** | `src/worker.ts` | logic ทั้งหมด: webhook LINE, คำสั่ง, Flex card, เรียก Supabase REST, dispatch export |
| **ข้อมูลประเทศ→สกุล** | `src/currency-by-country.ts` | COUNTRY_TO_CURRENCY, ISO_4217, FALLBACK_RATES, normalize helpers |
| **Supabase** | project **Trip-Manager** ref `zzglkhkyhkshjwbahuzv` | DB + storage (bucket `trip-exports` public) |
| **Excel export** | `scripts/export_trip_job.py` (รันใน GitHub Actions `export-trip.yml`) | สร้างไฟล์ .xlsx อัปขึ้น storage แล้ว push ลิงก์กลับ LINE |
| **Rich menu / QR icons** | `scripts/setup_richmenu.py`, `scripts/setup_qr_icons.py` | setup ครั้งเดียว (รูปเมนู/ไอคอน) |

**Deploy worker:** `npm run deploy` (wrangler). ต้องมี `CLOUDFLARE_API_TOKEN` ใน `.env`
URL: https://line-trip-bot.icecrowice.workers.dev
**Tests:** `npm test` (vitest, 41 เทส) · `npm run typecheck`
**Cron:** `* * * * *` (showtime), `0 2 * * *` = 09:00 ไทย (daily summary)

---

## ตาราง Supabase
`trips` · `expenses` · `daily_summaries` · `export_jobs` · `fx_rates` · `bot_states` · `showtimes` · `showtime_events` · `schedules`

- **trips**: title, status(active/closed), line_group_id, creator_id, base_currency, start_date, end_date, currency_code
- **expenses**: trip_id, payer_name, amount, currency, amount_thb, exchange_rate_used, exchange_rate_source, item_name, tag, participants[], slip_url
- **fx_rates**: currency (PK), rate_thb, updated_at — cache เรท ≤12 ชม.
- migration SQL อยู่ใน `db/*.sql`

---

## คำสั่งใน LINE

| คำสั่ง | ทำอะไร |
|---|---|
| `ทริป` / `trip` | สร้างทริป → ถามชื่อ → ถามประเทศ(ไทย/อังกฤษ/รหัสสกุล) → ถามช่วงวันที่ (date picker) |
| `บอล #ค่าข้าว 120 บอล ปาค` | เพิ่มรายจ่าย — **รูปแบบ: ผู้จ่าย #หมวด ยอด คนหาร...** (ชื่อแรก=ผู้จ่าย) |
| ส่งรูปสลิป → `บอล #ค่าข้าว บอล ปาค` | บันทึกจากสลิป (OCR ยอด) |
| `ยอด` / `ยอดวันนี้` | สรุป (Flex card, แปลงบาทเรทสด) |
| `edit` / `edit 0185 88` | ดูรายการล่าสุด / แก้ยอด |
| `history` → `excel 1` | ประวัติทริป + export |
| `excel` | export ทริปปัจจุบัน |
| `จบทริป` | ยืนยัน → ปิดทริป + สรุปโอนเงิน (net settlement) |
| `เมนู` / `help` / `showtime` | เมนู / ช่วยเหลือ / โหมดตารางโชว์ |

---

## ฟีเจอร์หลัก (logic สำคัญใน worker.ts)
- **แปลงบาทเรทสด**: `getRateThb()` cache fx_rates(12ชม)→ดึง er-api→fallback; `computeAmountThb()` ใช้ตอน save
- **ประเทศ→สกุล**: `resolveBaseCurrency()` รับชื่อประเทศ/รหัส ISO
- **net settlement**: `computeSettlement(paid, owed)` greedy โอนน้อยสุด (จบทริป + Excel)
- **Flex card**: `flexCard/flexKV/buildSaveCard` ทุก output ผลลัพธ์เป็นการ์ดหัวม่วง
- **Quick Reply ต่อโหมด**: `QR_MAIN/QR_NOTRIP/QR_COUNTRY/...` + `DEFAULT_QUICK` แนบทุก reply (ไอคอนสี host บน Supabase `trip-exports/qr/*.png`)
- **Excel**: 1 วัน 1 ชีต (Day N) + ชีต "รวมทุกวัน" (สรุปรายวัน/แปลงบาท/จ่ายไปแล้ว/โอนเงิน)

---

## รันสคริปต์ setup (ครั้งเดียว, บนเครื่อง local)
- ไอคอน QR: **รันแล้ว** (อัปขึ้น Supabase แล้ว) — แก้/เพิ่มไอคอน: `python scripts/setup_qr_icons.py` (pure stdlib ไม่ต้อง Pillow)
- Rich menu: **ยังไม่รัน** — `pip install Pillow; python scripts/setup_richmenu.py` (มีผลเฉพาะแชท 1:1 เท่านั้น)

---

## ข้อจำกัด / สิ่งที่ควรรู้ (debt)
1. **Rich Menu ไม่ขึ้นในกลุ่ม** (ข้อจำกัด LINE) → ในกลุ่มใช้ Quick Reply แทน; QR หายเมื่อกด/พิมพ์ กลับมาเมื่อบอตตอบ (LINE ไม่มี QR ถาวร/เด้งตอนพิมพ์)
2. **สีปุ่ม Quick Reply เปลี่ยนไม่ได้** (ตามธีมผู้ใช้) — เด่นได้แค่อิโมจิ/ไอคอนรูป
3. **ข้อความยืนยันยอดสลิป** ส่งแบบ push (async) → ยังไม่มี Quick Reply
4. **ทริปเก่า (ก่อนเพิ่ม payer-first)**: สลิปเก็บ payer_name เป็นชื่อทุกคนต่อกัน → net settlement เพี้ยนเฉพาะทริปเก่า
5. **เรท fallback อยู่ 2 ที่**: `FALLBACK_RATES` (worker, currency-by-country.ts) กับ `CURRENCY_RATES` (export python) — ใช้เฉพาะตอน API ล่ม; ถ้าแก้ควรซิงก์
6. **Excel คอลัมน์ "รายการ" = ชื่อหมวด** (ไม่มี item แยกหลังเปลี่ยนรูปแบบกรอก)
7. **`.env` local มีแค่ anon key** (service key อยู่ใน GitHub Actions secret); QR_ICON_BASE hardcode project ref
8. **daily cron** รัน 09:00 ไทย สรุป "เมื่อวาน"; เปลี่ยนเวลา = แก้ `0 2 * * *` ใน wrangler.toml + redeploy

---

## เอกสารออกแบบ
`docs/superpowers/specs/` และ `docs/superpowers/plans/` — spec/plan ของแต่ละฟีเจอร์ที่ทำในรอบนี้
