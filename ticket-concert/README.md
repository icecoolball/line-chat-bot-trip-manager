# Family Ticket Reminder

หน้าเว็บสำหรับครอบครัว ใช้ตรวจเวลาเว็บขายบัตร ตั้งเวลา countdown และสร้างรายการแจ้งเตือนเข้า LINE กลุ่มเดียว

## การทำงาน

- เปิดครั้งแรกด้วยลิงก์ `https://YOUR_DOMAIN/#invite=FAMILY_ACCESS_TOKEN`
- Browser แลก token เป็น HttpOnly cookie อายุ 30 วัน แล้วลบ token ออกจาก URL
- Render ให้บริการ dashboard และ API เท่านั้น
- Supabase Cron เรียก Edge Function ทุกนาทีเพื่อส่ง LINE ก่อนขาย 1 วัน, 1 ชั่วโมง, 30, 15 และ 5 นาที
- เก็บเวลาในฐานข้อมูลเป็น UTC และแสดงหน้าเว็บเป็น `Asia/Bangkok`

## รันในเครื่อง

1. คัดลอก `.env.example` เป็น `.env` และใส่ค่าจริง
2. รัน `npm ci`
3. รัน `npm start`
4. เปิด `http://localhost:5177/#invite=FAMILY_ACCESS_TOKEN`

## Environment ของ Render

- `FAMILY_ACCESS_TOKEN`: สุ่มอย่างน้อย 32 bytes; เปลี่ยนค่านี้เพื่อตัด session เดิมทุกเครื่อง
- `SUPABASE_URL`
- `SUPABASE_ANON_KEY`: publishable key ของโปรเจกต์ Trip-Manager
- `TICKET_BACKEND_TOKEN`: ค่าเดียวกับ `ticket_backend_token` ใน Supabase Vault; ใช้จำกัดสิทธิ์เฉพาะ ticket RPC
- `NODE_ENV=production`

## Supabase

1. Apply `supabase/migrations/20260624_ticket_reminders.sql`
2. Apply `supabase/migrations/20260624_ticket_scoped_rpc.sql`
3. Deploy `supabase/functions/ticket-reminders`
4. เก็บ `ticket_line_token`, `ticket_line_target` และ `ticket_backend_token` ใน Supabase Vault
5. เก็บ `ticket_project_url` และ `ticket_publishable_key` ใน Vault
6. สร้าง Cron ชื่อ `ticket-reminders-every-minute` ให้เรียก Edge Function ทุกนาที

## ตรวจสอบ

```powershell
npm test
node --check server.js
node --check public/app.js
```

ก่อนใช้งานจริง ให้ตั้ง schedule ล่วงหน้า 7 นาทีและยืนยันว่า LINE ได้รับข้อความเตือน 5 นาทีเพียงครั้งเดียว
