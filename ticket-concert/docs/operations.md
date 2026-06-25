# Ticket-Concert Operations

## Render deploy settings

- Root Directory: `ticket-concert`
- Build Command: `cd ticket-concert && npm ci`
- Start Command: `cd ticket-concert && npm start`

## First migration to per-member access

1. เก็บ secret `ticket_legacy_bootstrap_secret` ใน Supabase Vault ให้มีค่าเดียวกับ `FAMILY_ACCESS_TOKEN` เดิม
2. Apply migration `20260625_ticket_member_access_and_queue.sql`
3. Deploy Edge Function `ticket-reminders`
4. เปิดลิงก์ invite เดิม 1 ครั้งเพื่อสร้างสมาชิกคนแรก
5. ใช้ session ที่ได้ไปสร้าง personal invite ให้สมาชิกแต่ละคน
6. หลังทุกคนมีลิงก์ใหม่แล้ว ให้ rotate `FAMILY_ACCESS_TOKEN`

ผลที่คาดหวัง:
- สมาชิกคนแรกถูก bootstrap ได้ 1 คน
- หลังมีสมาชิก active แล้ว ลิงก์ shared เดิมจะไม่ใช้เป็น invite หลักอีก

## Member invite API

สร้างสมาชิกใหม่:

```powershell
Invoke-RestMethod `
  -Method POST `
  -Uri https://YOUR_DOMAIN/api/members `
  -Headers @{ cookie = "ticket_family_session=..." } `
  -ContentType "application/json" `
  -Body '{"name":"Alice"}'
```

ผลลัพธ์จะมี `inviteUrl` สำหรับส่งให้คนนั้นโดยตรง

ดูสมาชิก:

```powershell
Invoke-RestMethod `
  -Method GET `
  -Uri https://YOUR_DOMAIN/api/members `
  -Headers @{ cookie = "ticket_family_session=..." }
```

revoke สมาชิก:

```powershell
Invoke-RestMethod `
  -Method DELETE `
  -Uri https://YOUR_DOMAIN/api/members/MEMBER_ID `
  -Headers @{ cookie = "ticket_family_session=..." }
```

## Rotate `FAMILY_ACCESS_TOKEN`

1. สุ่ม secret ใหม่อย่างน้อย 32 bytes
2. อัปเดตค่า `FAMILY_ACCESS_TOKEN` ใน Render
3. redeploy service
4. ยืนยันว่า session เก่าใช้ `/api/session` หรือ `/api/schedules` ไม่ได้

## Rotate `TICKET_BACKEND_TOKEN`

1. สุ่ม secret ใหม่อย่างน้อย 32 bytes
2. อัปเดต `ticket_backend_token` ใน Supabase Vault
3. อัปเดตค่า `TICKET_BACKEND_TOKEN` ใน Render
4. redeploy service
5. ทดสอบ `GET /api/session`, `GET /api/schedules`, create schedule, delete schedule

## Queue runtime

- reminder ใหม่จะถูก enqueue เข้า `pgmq` queue ชื่อ `ticket_reminders`
- Cron ยังเรียก Edge Function ทุกนาที แต่หน้าที่เปลี่ยนเป็นอ่าน queue และส่ง LINE
- ถ้าส่งสำเร็จ message จะถูกลบจาก queue
- ถ้าส่งไม่สำเร็จ message จะ retry ผ่าน queue visibility window
- ถ้าพลาดครบ 5 ครั้ง message จะถูก mark เป็น `skipped`

## Production smoke checklist

1. `/healthz` ต้องได้ `{"ok":true}`
2. `/api/schedules` ไม่มี session ต้องได้ `401`
3. เปิด personal invite link ใหม่แล้วเข้า dashboard ได้
4. revoke member แล้ว session ของคนนั้นต้องใช้ต่อไม่ได้
5. ตรวจเวลา Eventpop ได้
6. ตั้ง schedule ล่วงหน้า 7 นาที
7. ต้องได้รับ LINE เตือน 5 นาทีเพียง 1 ครั้ง
8. Refresh dashboard แล้วสถานะ reminder ต้องตรงกับที่ส่งจริง
