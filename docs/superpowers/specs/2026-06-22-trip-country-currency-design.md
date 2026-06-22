# เลือกประเทศแทนสกุลเงินตอนสร้างทริป + แปลง THB สดในยอดสรุป

วันที่: 2026-06-22

## เป้าหมาย

1. ตอนสร้างทริป ให้ผู้ใช้พิมพ์ **ชื่อประเทศ** (ไทย/อังกฤษ) หรือ **รหัสสกุลเงิน** ก็ได้ แล้วระบบ map เป็นสกุลเงินหลักของทริป — รองรับทุกประเทศ
2. คำสั่ง **ยอดวันนี้ / ยอดรวม / จบทริป** แสดงยอดเป็นสกุลเงินของทริป คั่นด้วย `|` ตามด้วยยอดที่แปลงเป็น THB ด้วยเรท **สด ณ เวลาที่ถาม**
3. เรทที่ดึงจากเว็บถูก cache ไว้ใน Supabase เพื่อลดการยิง API

## ขอบเขต

**ทำ:** trip creation flow, การ resolve สกุลเงิน, การแปลง THB สดในยอดสรุป 3 จุด (ยอดวันนี้/ยอดรวม/จบทริป), cache เรทใน Supabase

**ไม่แตะ (ตามที่ตกลง):** slip flow, Excel export (แปลงถูกอยู่แล้ว), showtime, `computeAmountThb` ตอน save expense, daily-summary cron

## สถาปัตยกรรม

### ไฟล์ใหม่: `src/currency-by-country.ts`

เป็น data module ล้วน (ไม่มี side effect) นำเข้าโดย `worker.ts`

```ts
// ประเทศ (ไทย + อังกฤษ + alias เช่น อเมริกา/สหรัฐ/usa/us) -> รหัส ISO 4217
// key ถูก normalize ด้วย normalizeCountryName ก่อนเก็บ
export const COUNTRY_TO_CURRENCY: Record<string, string>;

// รหัสสกุลเงิน ISO 4217 ที่ใช้งานได้ทั้งหมด (ไว้ validate)
export const ISO_4217: Set<string>;

// เรท -> THB ตั้งต้น (fallback เมื่อ API ล่มและไม่มี cache) superset ของ THB/JPY/USD/KRW
export const FALLBACK_RATES: Record<string, number>;

// normalize ชื่อประเทศ: lowercase, trim, ยุบช่องว่าง, ตัดคำนำหน้า "ประเทศ"/"the "
export function normalizeCountryName(input: string): string;

// normalize รหัสสกุล: uppercase + แก้สะกดผิด/สัญลักษณ์ (JYP->JPY, ¥->JPY, ₩->KRW, $->USD, ฿->THB)
// คืนรหัส (อาจไม่ valid -> ผู้เรียกเช็คกับ ISO_4217 เอง)
export function normalizeCurrencyCode(input: string | null | undefined): string;
```

หน้าที่: ตอบได้ว่า "ประเทศ/รหัสนี้คือสกุลอะไร" และ "รหัสนี้ valid ไหม" โดยไม่ต้องพึ่ง network หรือ DB

### ตารางใหม่ใน Supabase: `fx_rates`

cache เรทที่ดึงมาจากเว็บ (worker เขียนไฟล์ source ตัวเองไม่ได้ จึง cache ที่นี่แทน)

```sql
create table if not exists fx_rates (
  currency   text primary key,
  rate_thb   numeric not null,
  updated_at timestamptz not null default now()
);
```

### worker.ts — ฟังก์ชันใหม่

```ts
// ชื่อประเทศ/รหัสสกุล -> รหัส ISO ที่ valid หรือ null
function resolveBaseCurrency(input: string): string | null
//  1. code = normalizeCurrencyCode(input); ถ้า ISO_4217.has(code) -> code
//  2. key  = normalizeCountryName(input); ถ้า COUNTRY_TO_CURRENCY[key] -> ค่านั้น
//  3. มิฉะนั้น null

// ดึงเรท THB ต่อ 1 หน่วยของ currency: cache(Supabase) -> สด(er-api) -> fallback(file)
async function getRateThb(env: Env, currency: string): Promise<number>
//  - curr = normalizeCurrencyCode(currency); ถ้า "THB" -> 1
//  - อ่าน fx_rates; ถ้า fresh (< 12 ชม.) คืน rate_thb
//  - ดึงสดจาก https://open.er-api.com/v6/latest/<curr> (อ่าน rates.THB) ผ่าน fetchWithTimeout
//  - สำเร็จ -> supabaseUpsert fx_rates {currency, rate_thb, updated_at}, คืนค่า
//  - ล้มเหลว -> cache เก่า (ถ้ามี) -> FALLBACK_RATES[curr] -> 1 (กันพัง)

// ดึงเรทของหลายสกุลครั้งเดียว (กันยิงซ้ำในการสรุปยอดครั้งเดียว)
async function getRatesForCurrencies(env: Env, currencies: string[]): Promise<Map<string, number>>
```

`getRateThb` ใช้ `supabaseSelect`/`supabaseUpsert`/`fetchWithTimeout` ที่มีอยู่แล้ว

## Data flow

### สร้างทริป
```
ผู้ใช้พิมพ์ชื่อทริป -> prompt ถามประเทศ -> ผู้ใช้พิมพ์ "ญี่ปุ่น" หรือ "JPY"
  -> resolveBaseCurrency -> "JPY" -> เก็บ trips.base_currency = "JPY"
  -> ถ้า null -> ตอบ error ให้ลองใหม่
```

### ถามยอด (วันนี้/รวม/จบทริป)
```
ดึง expenses -> หาสกุลที่ปรากฏ (normalizeCurrencyCode) -> getRatesForCurrencies -> rateMap
  -> ต่อ expense: thb = Number(amount) * (rateMap.get(curr) ?? 1)   // แปลงสด ไม่ใช้ amount_thb ที่เก็บ
  -> รวมต่อสกุล (ยอดเดิม) + รวม THB + แบ่งหมวด/รายคนด้วย THB สด
```

## จุดที่แก้ใน worker.ts

| จุด | เดิม | ใหม่ |
|---|---|---|
| prompt (`:133`, `:173`) | "ระบุสกุลเงินหลัก เช่น THB/JPY/USD/KRW" | "ระบุประเทศ เช่น ญี่ปุ่น / เกาหลี หรือรหัสสกุล เช่น JPY" |
| `handleTripCurrency` (`:176`) | `normalizeCurrency(text)` | `resolveBaseCurrency(text)`; error ใหม่ |
| `getTripBaseCurrency` (`:771`) | จำกัด 4 สกุล -> THB | คืน base_currency ถ้า `ISO_4217.has(...)` มิฉะนั้น "THB" |
| `parseExpense` (`:734`) | default ผ่าน `normalizeCurrency` (จำกัด 4) | token กลางบรรทัด **ยังเข้ม 4 สกุล** (กันชื่อเล่นชนรหัส); default เชื่อสกุลทริปถ้าเป็น ISO valid |
| `buildTodayMessage` (`:829`) | total จาก `amount_thb` ที่เก็บ | ต่อสกุล `{ยอดเดิม} {CUR} \| {THB} บาท` + รวมบาท (แปลงสด) |
| `buildTripTotalMessage` (`:814`) | `รวม X บาท` | เพิ่มบรรทัดต่อสกุล + รวมบาท (แปลงสด) |
| `buildEndTripSummary` (`:852`) | หารด้วย `amount_thb` ที่เก็บ | หารด้วย THB แปลงสด |

## ตัวอย่างผลลัพธ์

```
ยอดวันนี้ (2026-06-22)
JPY 50,000 | 10,200.75 บาท
THB 2,000 | 2,000 บาท
รวมวันนี้: 12,200.75 บาท

#ค่าข้าว 8,200 บาท บอล เบียร์
```

## Error handling

- ดึงเรทไม่ได้ + ไม่มี cache + ไม่มีใน FALLBACK_RATES -> ใช้เรท 1 (แสดงยอดเดิมเป็นบาท) ไม่ทำให้คำสั่งพัง
- ผู้ใช้พิมพ์ประเทศ/รหัสที่ไม่รู้จักตอนสร้างทริป -> ตอบให้ลองใหม่ พร้อมตัวอย่าง
- `fetchWithTimeout` ตั้ง timeout สั้น (เช่น 5 วินาที) กันค้าง

## ข้อจำกัดที่ยอมรับ

- `computeAmountThb` ตอน save ยังเก็บ `amount_thb` ด้วยเรทคงที่ 4 สกุล -> ค่าที่เก็บอาจไม่ตรงสำหรับสกุลอื่น แต่ยอดสรุปทั้งหมด**ไม่ใช้ค่านี้แล้ว** (แปลงสด) และ Excel export ก็แปลงสดเอง จึงไม่กระทบผู้ใช้
- daily-summary cron ยังใช้ `amount_thb` ที่เก็บ (อยู่นอก scope)
- override สกุลแบบ inline ต่อรายการ (เช่น "coffee 5 EUR") จำกัด 4 สกุลเหมือนเดิม (กัน collision ชื่อเล่น)

## Testing

- `resolveBaseCurrency`: "ญี่ปุ่น"/"japan"/"JPY"/"jpy" -> "JPY"; "อเมริกา"/"usa"/"สหรัฐ" -> "USD"; ขยะ -> null
- `normalizeCurrencyCode`: "JYP"->"JPY", "¥"->"JPY", "฿"->"THB"
- `getRateThb`: cache fresh ใช้ cache; cache เก่า -> ดึงสด + upsert; API ล่ม -> fallback
- ยอดสรุป: ทริป JPY คำนวณ THB จากเรทใน rateMap ถูกต้อง; หลายสกุลรวมถูก
- token collision: "ข้าว 100 TOP เบียร์" -> TOP เป็นคน ไม่ใช่สกุลเงิน
```
