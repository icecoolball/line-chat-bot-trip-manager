# ผู้จ่ายต่อรายการ + สรุปโอนเงิน (net settlement)

วันที่: 2026-06-23

## เป้าหมาย

1. บันทึก "ผู้จ่ายจริง" ต่อรายการได้ทั้งทางพิมพ์ปกติและสลิป ด้วยกติกาเดียวกัน: **ชื่อแรก = ผู้จ่าย**
2. ตอน `จบทริป` เพิ่มสรุป "ใครจ่ายไปแล้วเท่าไร" และ "สรุปโอนเงิน" (ใครต้องโอนให้ใครเท่าไร) แบบจำนวนการโอนน้อยที่สุด

## กติกาการกรอก (แบบ B — ผู้จ่ายชื่อแรก, ใช้ #หมวด แทนรายการ, ใช้รูปแบบใหม่อย่างเดียว)

รูปแบบเดียวกันทั้งพิมพ์ปกติและสลิป: **`<ผู้จ่าย> #หมวด [ยอด] <คนหาร...>`**

- **พิมพ์ปกติ:** `บอล #ค่าข้าว 120 บอล ปาค มิน` → ผู้จ่าย=บอล, หมวด=#ค่าข้าว, ยอด=120, หาร บอล ปาค มิน
  - **ไม่มีช่อง "รายการ" แยกแล้ว** — `item_name` = ชื่อหมวด (กระทบคอลัมน์ "รายการ" ใน Excel ที่จะโชว์ชื่อหมวด)
- **สลิป:** `บอล #ค่าข้าว บอล ปาค มิน` → ผู้จ่าย=บอล, หาร บอล ปาค มิน (ยอดมาจาก OCR/พิมพ์)
- จ่ายแต่ไม่ได้กิน: ไม่ต้องใส่ชื่อตัวเองในคนหาร เช่น `บอล #ค่าข้าว 120 ปาค มิน`
- **รูปแบบเก่าเลิกใช้** (ไม่มี backward compat) — ถ้าไม่ใส่ผู้จ่ายเป็นชื่อแรก ผู้จ่ายจะ default เป็นชื่อ LINE คนพิมพ์

### parseExpense ใหม่ (canonical)
- หา amount (ตัวเลขตัวแรก), tag (token แรกที่ขึ้นต้น #, อยู่ที่ไหนก็ได้), currency (strict 4 ตัว ถ้าอยู่ติดหลัง amount)
- ชื่อ (token ที่ไม่ใช่ amount/tag/currency): ก่อน amount → ตัวแรก = ผู้จ่าย (ถ้าไม่มี → default คนพิมพ์); หลัง amount → คนหาร
- `item_name` = ชื่อหมวด (tag ตัด #) หรือ "ค่าใช้จ่าย" ถ้าไม่มีหมวด

## ขอบเขต

**ทำ:**
- `parseExpense` — rewrite เป็น canonical `<ผู้จ่าย> #หมวด <ยอด> <คนหาร...>` (#หมวด แทนรายการ)
- `parseSlipAssignment` — ชื่อแรก = payer, ที่เหลือ = participants (เดิม payer = ชื่อทุกคนต่อกัน ซึ่งใช้ไม่ได้)
- ข้อความ prompt สลิป ([:205](src/worker.ts:205)) + help text ([:1179](src/worker.ts:1179)) — อธิบายรูปแบบใหม่
- `buildEndTripSummary` — เพิ่มส่วน "จ่ายไปแล้ว" + "สรุปโอนเงิน"
- ฟังก์ชันใหม่ `computeSettlement(paid, owed)` (pure, export, มี unit test) + helper `fmt2`

**ไม่แตะ:**
- ส่วน "ยอดต้องจ่ายตามหมวด" / "ยอดต่อคน" เดิมใน end trip — คงไว้ (ยอดต่อคนไม่ปัดเศษตามที่ตกลง)
- export, summaries อื่น, computeAmountThb

## net settlement

ต่อคน: `คงเหลือ = จ่ายจริง(THB) − ยอดที่ต้องจ่าย(THB)`
- จ่ายจริง = ผลรวม `expenseThbLive` ของรายการที่ `payer_name` = คนนั้น
- ยอดที่ต้องจ่าย = `totalByPerson` ที่คำนวณอยู่แล้ว (ส่วนแบ่งการกิน)

อัลกอริทึม greedy (จำนวนการโอนน้อยสุด):
```ts
export function computeSettlement(
  paid: Record<string, number>, owed: Record<string, number>,
): Array<{ from: string; to: string; amount: number }> {
  const names = new Set([...Object.keys(paid), ...Object.keys(owed)]);
  const creditors: Array<{ name: string; amt: number }> = [];
  const debtors: Array<{ name: string; amt: number }> = [];
  for (const n of names) {
    const net = (paid[n] || 0) - (owed[n] || 0);
    if (net > 0.01) creditors.push({ name: n, amt: net });
    else if (net < -0.01) debtors.push({ name: n, amt: -net });
  }
  creditors.sort((a, b) => b.amt - a.amt);
  debtors.sort((a, b) => b.amt - a.amt);
  const transfers: Array<{ from: string; to: string; amount: number }> = [];
  let i = 0, j = 0;
  while (i < debtors.length && j < creditors.length) {
    const pay = Math.min(debtors[i].amt, creditors[j].amt);
    transfers.push({ from: debtors[i].name, to: creditors[j].name, amount: pay });
    debtors[i].amt -= pay; creditors[j].amt -= pay;
    if (debtors[i].amt < 0.01) i++;
    if (creditors[j].amt < 0.01) j++;
  }
  return transfers;
}
```
ยอดโอนแสดง **ทศนิยม 2 ตำแหน่ง** (`fmt2`, เช่น 527.55)

## ตัวอย่างผลลัพธ์ จบทริป

```
ทริป: เยอรมัน 23062026

ยอดต้องจ่ายตามหมวด:
#ค่าข้าว
- บอล: 1,620.333 บาท
...

ยอดรวมทั้งทริป: 19,933.87 บาท
บอล: 5,388.55 บาท
...

จ่ายไปแล้ว:
- บอล: 4,861.00 บาท
- ปาค: 15,072.87 บาท

สรุปโอนเงิน 💸
- บอล → ปาค: 527.55 บาท
- พี่เล็ก → ปาค: 1,620.33 บาท
- เอ้ → ปาค: 3,768.22 บาท
- มิน → ปาค: 3,768.22 บาท
```

## ข้อจำกัด/หมายเหตุ

- รายการ**สลิปเก่า**ที่บันทึกก่อนแก้ จะมี `payer_name` เป็นชื่อทุกคนต่อกัน → ในสรุปโอนเงินจะเพี้ยนเฉพาะทริปเก่า (ทริปใหม่ถูกต้อง)
- ถ้าผู้ใช้ไม่ใส่ผู้จ่ายเป็นชื่อแรกในสลิป → ชื่อแรกในลิสต์จะถูกถือเป็นผู้จ่าย (prompt จะอธิบายรูปแบบให้)

## Testing

- `computeSettlement`: เคสเจ้าหนี้คนเดียว/หลายคน, สมดุล (paid=owed → ไม่มีโอน), ปัดเศษ epsilon
- typecheck + manual ใน LINE (ส่งสลิปรูปแบบใหม่ + จบทริป)
