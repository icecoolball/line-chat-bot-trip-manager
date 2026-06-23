# ช่วงวันที่ทริป + Day labels ใน Excel + แก้ daily cron

วันที่: 2026-06-23

## เป้าหมาย

1. ตอนเปิดทริป ถาม "ช่วงวันที่ (เริ่ม-จบ)" หลังตอบประเทศ แล้วเก็บลง DB
2. Excel: ตั้งชื่อชีตรายวันเป็น `Day N · DD/MM` นับจาก start_date; สร้างชีตของวันที่ยังไม่มีรายจ่ายด้วย (ภายในช่วงทริป)
3. แก้ daily-summary cron: สรุปยอดของ **เมื่อวาน** (วันที่ผ่านมาเต็มวัน) ส่งตอน 9 โมงเช้าไทย

## รายละเอียด

### 1. ถามช่วงวันที่ตอนเปิดทริป
- หลัง `handleTripCurrency` resolve สกุลได้ → ไม่ insert ทันที แต่ set state `wait_trip_dates` แล้วถามช่วงวันที่
- prompt: `ทริปช่วงวันไหน? เช่น 23/06/2026-27/06/2026 (หรือพิมพ์วันเริ่มอย่างเดียว / พิมพ์ ข้าม)`
- parser `parseTripDates(text)` (export, มี unit test):
  - หา substring วันที่ด้วย regex `(\d{1,4})[\/-](\d{1,2})[\/-](\d{1,4})`
  - ตัวแรกที่เป็น 4 หลัก = ปี (YYYY-MM-DD) มิฉะนั้น DD/MM/YYYY
  - ปี < 100 → +2000; ปี > 2400 → -543 (พ.ศ.→ค.ศ.)
  - validate เดือน 1-12, วัน 1-31
  - คืน `{ start, end }` (ISO) — match แรก=start, match ที่สอง=end (ถ้าไม่มี end → null)
  - `ข้าม`/`-`/`skip` → `{ start: null, end: null }`
  - หาวันที่ไม่ได้และไม่ใช่ "ข้าม" → คืน null (ให้ตอบ error พิมพ์ใหม่)
- เก็บ `start_date`, `end_date` ตอน insert trips; ข้อความยืนยันแสดงช่วง + จำนวนวัน

### 2. DB migration
```sql
alter table trips add column if not exists start_date date;
alter table trips add column if not exists end_date date;
```

### 3. Excel — Day labels (export_trip_job.py)
- โหลด `trip.start_date` (ถ้ามี)
- ชื่อชีตรายวัน: ถ้ามี start_date → `Day {n} · {DD/MM}` (n = (วันที่-start_date)+1); ถ้าไม่มี → ใช้วันที่ดิบเหมือนเดิม
- ถ้ามี start_date & end_date → สร้างชีตทุกวันในช่วง (วันที่ไม่มีรายจ่าย = ชีตว่างมีแต่หัวตาราง + "รวมวันนี้ (บาท) 0")
- ชีตสุดท้าย "รวมทุกวัน" เหมือนเดิม (สรุปรายวันจะครอบทุกวันในช่วง)

### 4. daily cron — สรุปเมื่อวาน
- `runDailySummary`: เปลี่ยนจากสรุป "วันนี้" เป็น "เมื่อวาน"
  - `yesterday = thaiDateString(new Date(Date.now() - 24*3600*1000))`
  - filter expenses ที่ `thaiDateFromIso(created_at) === yesterday`
  - ข้อความ "สรุปยอดประจำวัน (yesterday)"
  - เก็บ daily_summaries ด้วย summary_date = yesterday
- cron ยังคง `0 2 * * *` (= 09:00 ไทย) — เปลี่ยนเวลาส่ง = แก้ค่านี้ใน wrangler.toml + redeploy

## ขอบเขต / ไม่แตะ
- ทริปเก่าที่ไม่มี start_date → Excel ใช้วันที่ดิบ (ไม่มี Day label) — ทำงานได้ปกติ
- การตั้งเวลาส่ง cron แบบ runtime/ต่อทริป = ไม่รองรับ (ข้อจำกัด Cloudflare cron)

## Testing
- `parseTripDates`: ช่วง DD/MM/YYYY, วันเดียว, YYYY-MM-DD, พ.ศ., ข้าม, ขยะ→null
- Excel: ทริปมี start/end → ชีต Day 1..N ครบรวมวันว่าง
- cron: สรุปเมื่อวาน (manual/observe)
