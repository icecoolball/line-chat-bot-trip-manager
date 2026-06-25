# Ticket-Concert Operations

## Render deploy settings

- Root Directory: `ticket-concert`
- Build Command: `cd ticket-concert && npm ci`
- Start Command: `cd ticket-concert && npm start`
- หลังเปลี่ยน env ให้ redeploy ทุกครั้ง

## Rotate `FAMILY_ACCESS_TOKEN`

1. สร้าง token ใหม่อย่างน้อย 32 bytes
2. อัปเดตค่า `FAMILY_ACCESS_TOKEN` ใน Render
3. redeploy service
4. เปิด `/#invite=NEW_TOKEN` บนอุปกรณ์ที่ต้องการใช้งาน
5. ยืนยันว่า session เก่าเข้า `/api/schedules` ไม่ได้แล้ว

ผลที่คาดหวัง:
- ลิงก์ invite ใหม่เข้าได้
- cookie ใหม่ถูกสร้าง
- session เดิมทุกเครื่องหมดอายุทันที

## Rotate `TICKET_BACKEND_TOKEN`

1. สร้าง token ใหม่อย่างน้อย 32 bytes
2. อัปเดต `ticket_backend_token` ใน Supabase Vault
3. อัปเดตค่า `TICKET_BACKEND_TOKEN` ใน Render
4. redeploy service
5. ทดสอบ create, list, และ delete schedule จาก dashboard

ผลที่คาดหวัง:
- dashboard โหลดรายการได้
- สร้าง schedule ใหม่ได้
- ลบ schedule ได้

## Production smoke checklist

1. เปิด `/healthz` ต้องได้ `{"ok":true}`
2. เปิด `/api/schedules` โดยไม่มี session ต้องได้ `401`
3. เปิดลิงก์ `/#invite=...` ใหม่แล้วเข้า dashboard ได้
4. กดตรวจเวลา Eventpop แล้วต้องอ่านเวลาได้หรือได้ matched text ที่ใช้ parse ต่อได้
5. ตั้ง schedule ล่วงหน้า 7 นาที
6. ต้องได้รับ LINE แจ้งเตือน 5 นาทีเพียง 1 ครั้ง
7. Refresh dashboard แล้วสถานะ reminder ต้องอัปเดตตรงกับที่ส่งจริง

## Dependency note

ณ ตอนอัปเดตเอกสารรอบนี้ `npm audit` ของ `ticket-concert` ไม่พบ vulnerability ค้าง
