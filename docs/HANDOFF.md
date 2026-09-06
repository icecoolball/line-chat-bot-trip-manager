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
| `edit 0185 name ค่าที่พัก` | แก้ชื่อรายการในทริปปัจจุบัน โดยคงยอดและคนหารเดิม |
| `edit 0197 people บอล ปาค มิน เอ้ ไท จอม` | แทนที่คนหารทั้งรายการด้วยรายชื่อนี้ โดยคงยอดและผู้จ่ายเดิม |
| `มัดจำ` / `deposit` | วิธีใช้คำสั่งมัดจำรายคน |
| `มัดจำ 0206` | ดูยอดสะสมที่แต่ละคนจ่ายแล้ว ยอดค้าง และยอดเกิน |
| `มัดจำ 0206 560 บอล ปาค มิน ให้ บอล` | ตั้งยอดสะสมคนละ 560 ในสกุลเงินของรายการ โดยบอลเป็นคนรวบรวม |
| `มัดจำ 0206 0 ปาค ให้ บอล` | แก้ยอดสะสมของปาคเป็นศูนย์ |
| `history` → `excel 1` | ประวัติทริป + export |
| `excel` | export ทริปปัจจุบัน |
| `จบทริป` | ยืนยัน → ปิดทริป + สรุปโอนเงิน (net settlement) |
| `เมนู` / `help` / `showtime` | เมนู / ช่วยเหลือ / โหมดตารางโชว์ |

ข้อความที่ไม่ตรงคำสั่งหรือรูปแบบรายจ่ายจะไม่มีข้อความเตือนตอบกลับ หากอยู่ระหว่างกรอกข้อมูล เช่น ชื่อทริปหรือยืนยันสลิป บอตยังรับข้อมูลและตอบตามขั้นตอนเดิม

### มัดจำรายคน

- ตัวเลขในคำสั่งคือ **ยอดสะสมต่อคน** ไม่ใช่ยอดเพิ่ม พิมพ์ซ้ำจะไม่บวกเงินซ้ำ ถ้าจ่ายเพิ่มจาก 560 เป็นรวม 800 ให้พิมพ์ยอด 800
- ระบุชื่อผู้รับหลัง `ให้` ทุกครั้ง ชื่อผู้จ่ายต้องตรงกับคนหาร และผู้รับต้องเป็นคนหารหรือผู้จ่ายของรายการ
- ถ้าผู้จ่ายกับผู้รับเป็นคนเดียวกัน เป็นการกันเงินส่วนตัวไว้ ไม่ปรับยอดโอนซ้ำ
- มัดจำไม่เพิ่มค่าที่พักหรือยอดค่าใช้จ่าย สรุปจบทริปและ Excel จะหักเงินโอนที่บันทึกแล้ว โดย Excel มีชีต `เงินมัดจำ` แยก
- ยอดค้างและยอดเกินเทียบกับส่วนแบ่งเต็มของค่าใช้จ่าย ไม่ได้ตั้งเป้ามัดจำ 50% ให้อัตโนมัติ ตรวจให้แน่ใจว่ายอดค่าใช้จ่ายเดิมเป็นยอดเต็มก่อนอ่านสถานะ
- เก็บสถานะล่าสุดต่อคน เปลี่ยนจำนวนเงินหรือผู้รับคือการแก้ข้อมูลเดิม ไม่มีประวัติรายการโอน/คืนเงินแยกแต่ละครั้ง
- คำสั่งเขียนได้เฉพาะรายการของทริปที่ยังเปิดในแชทปัจจุบัน ข้อความเก่าที่ LINE ส่งซ้ำไม่ทับยอดใหม่
- แก้คนหารใช้ `edit [ID] people [ชื่อคนหารทั้งหมด]` ต้องพิมพ์ทุกคนที่ต้องการหาร ระบบจะไม่ให้เอาชื่อที่ยังมีมัดจำหรือรับเงินมัดจำออก ให้แก้ยอดที่เกี่ยวข้องเป็น 0 ก่อน; ชื่อผู้รับที่เป็นผู้จ่ายค่าใช้จ่ายเดิมยังคงได้
- เมื่อยืนยันจบทริป ระบบปิดรับมัดจำและการแก้คนหารก่อนอ่านสรุปสุดท้าย ถ้าอ่านสรุปไม่สำเร็จให้พิมพ์ยืนยันซ้ำเพื่ออ่านยอดของทริปที่ปิดแล้ว

### เปิดใช้รุ่นมัดจำ (ต้องได้รับอนุญาตก่อนเปลี่ยนระบบจริง)

1. ยืนยันว่า Worker/Excel/บริการเดิมใช้ service-role key: migration จะถอนสิทธิ์สาธารณะของ `trips`, `expenses`, `bot_states`, `export_jobs` เพื่อป้องกันปลอมข้อมูลเจ้าของทริปและงาน export; client ที่ใช้ anon อ่าน/เขียนตารางเหล่านี้โดยตรงต้องย้ายไปใช้ backend ก่อน
2. Apply `db/2026-09-06-expense-deposits.sql` เพื่อเพิ่มตาราง/RPC ที่อนุญาตเฉพาะ `service_role` ไม่เติมหรือแก้ยอดข้อมูลเดิม
3. เผยแพร่ Worker กับ Python exporter รุ่นนี้ร่วมกัน เพื่อให้การหักมัดจำตรงกัน ใช้ service-role key ในทั้งสองระบบ; `/api/export-trip` ต้องส่ง `Authorization: Bearer <CRON_SECRET>` และจะปฏิเสธถ้ายังไม่ตั้ง secret
4. ตรวจคำสั่งดูยอด บันทึก แก้ยอด และ Excel ในทริปทดสอบที่อนุญาต ไม่ใช้รายการจริงเพื่อทดลอง

ทดสอบ: `npm test`, `npm run typecheck`, `python -m unittest test/test_fallback_rates.py test/test_deposit_export.py` (ต้องมี pandas/openpyxl); SQL ใช้ `node --test test/deposits-db.test.mjs` โดยติดตั้ง PGlite ใน runtime ทดสอบและกำหนด `PGLITE_MODULE` ไปยังไฟล์ `dist/index.js` ของแพ็กเกจ ทดสอบทั้งหมดเป็น local ไม่แก้ฐานข้อมูลจริง

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
