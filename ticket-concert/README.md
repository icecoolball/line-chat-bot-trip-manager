# Family Ticket Reminder

หน้าเว็บสำหรับครอบครัว ใช้ตรวจเวลาเว็บขายบัตร ตั้งเวลา countdown และส่งเตือนเข้า LINE โดยยังใช้สแต็กฟรีเดิมคือ Render + Supabase

## การทำงาน

- สมาชิกแต่ละคนใช้ invite link ของตัวเองในรูป `https://YOUR_DOMAIN/#invite=PERSONAL_INVITE_TOKEN`
- Browser แลก invite เป็น HttpOnly session cookie อายุ 30 วัน
- Server ตรวจว่า member ยัง active อยู่ทุก request
- Server สร้าง signed RPC credential อายุสั้นต่อ request ก่อนคุยกับ Supabase
- Supabase Queue เก็บ reminder jobs และ Edge Function เป็นตัวส่ง LINE

## รันในเครื่อง

1. คัดลอก `.env.example` เป็น `.env`
2. ใส่ `FAMILY_ACCESS_TOKEN`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `TICKET_BACKEND_TOKEN`
3. รัน `npm ci`
4. รัน `npm start`

## Environment ของ Render

- `FAMILY_ACCESS_TOKEN`: ใช้ sign session cookie และใช้เป็น legacy bootstrap token ชั่วคราวตอนย้ายจากของเดิม
- `SUPABASE_URL`
- `SUPABASE_ANON_KEY`
- `TICKET_BACKEND_TOKEN`: shared signing secret สำหรับ signed RPC credential; ต้องตรงกับ secret `ticket_backend_token` ใน Supabase Vault
- `NODE_ENV=production`

## Deploy บน Render

- Root Directory: `ticket-concert`
- Build Command: `cd ticket-concert && npm ci`
- Start Command: `cd ticket-concert && npm start`

## Supabase

1. Apply `supabase/migrations/20260624_ticket_reminders.sql`
2. Apply `supabase/migrations/20260624_ticket_scoped_rpc.sql`
3. Apply `supabase/migrations/20260625_ticket_member_access_and_queue.sql`
4. Apply `supabase/migrations/20260712_ticket_schedule_confirmation.sql`
5. เก็บ `ticket_line_token`, `ticket_line_target`, `ticket_backend_token` ใน Supabase Vault
6. ตอน migration รอบแรก ให้เก็บ `ticket_legacy_bootstrap_secret` ใน Vault โดยใช้ค่าเดียวกับ `FAMILY_ACCESS_TOKEN` เดิม
7. Deploy `supabase/functions/ticket-reminders`
8. Cron ยังเรียก `ticket-reminders` ทุกนาทีเหมือนเดิม

## ตรวจสอบ

```powershell
npm audit
npm test
node --check server.js
node --check public/app.js
```

ดูขั้นตอน bootstrap สมาชิกคนแรก, สร้าง invite ต่อคน, rotate secret, และ production smoke test ที่ [docs/operations.md](./docs/operations.md)
