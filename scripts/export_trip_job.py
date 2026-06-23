import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

import pandas as pd
import requests
from supabase import create_client


# เรทเทียบบาทแบบคงที่ (fallback สุดท้ายถ้าดึงเรทเรียลไทม์ไม่ได้) — ตรงกับ src/worker.ts
CURRENCY_RATES = {"THB": 1.0, "JPY": 0.23, "USD": 34.5, "KRW": 0.025}

# map สกุลเงินที่สะกดผิด/ใช้ตัวย่อไม่มาตรฐาน ให้เป็นรหัส ISO ที่ถูกต้อง
CURRENCY_ALIASES = {
    "JYP": "JPY", "JPN": "JPY", "YEN": "JPY", "¥": "JPY",
    "WON": "KRW", "₩": "KRW",
    "USD$": "USD", "$": "USD",
    "฿": "THB", "BAHT": "THB",
}

# cache เรทเรียลไทม์ต่อการรัน 1 ครั้ง: สกุล -> เรทเทียบบาท (None = ดึงไม่ได้)
_FX_CACHE = {}


def normalize_currency(value):
    """แปลงค่าสกุลเงินให้เป็นรหัสมาตรฐาน (แก้คำสะกดผิด เช่น JYP -> JPY)."""
    curr = (value or "THB").strip().upper()
    return CURRENCY_ALIASES.get(curr, curr)


def fetch_fx_rate_to_thb(currency):
    """ดึงเรทเรียลไทม์ (THB ต่อ 1 หน่วยของ currency) จาก open.er-api.com คืน None ถ้าล้มเหลว."""
    curr = (currency or "").upper()
    if curr == "THB":
        return 1.0
    if curr in _FX_CACHE:
        return _FX_CACHE[curr]
    rate = None
    try:
        res = requests.get(f"https://open.er-api.com/v6/latest/{curr}", timeout=10)
        if res.ok:
            data = res.json()
            if data.get("result") == "success":
                value = (data.get("rates") or {}).get("THB")
                if isinstance(value, (int, float)) and value > 0:
                    rate = float(value)
    except Exception:
        rate = None
    _FX_CACHE[curr] = rate
    return rate


def require_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing {name}")
    return value


def as_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compute_thb(amount, currency):
    """คืน (amount_thb, rate, source) โดยดึงเรทเรียลไทม์ตอน export ทุกแถว
    ถ้าดึงไม่ได้ค่อย fallback เป็นเรทคงที่."""
    curr = normalize_currency(currency)
    amount_val = as_float(amount) or 0.0
    if curr == "THB":
        return round(amount_val, 2), 1.0, "same_currency"
    live_rate = fetch_fx_rate_to_thb(curr)
    if live_rate:
        return round(amount_val * live_rate, 2), live_rate, "er-api_export"
    rate = CURRENCY_RATES.get(curr)
    if rate:
        return round(amount_val * rate, 2), rate, "fallback_export"
    # ไม่รู้จักสกุลนี้และดึงเรทไม่ได้ -> ไม่แปลง (กันได้เรท 1 มั่ว ๆ)
    return round(amount_val, 2), None, "unknown_currency"


def compute_settlement(paid, owed):
    """จับคู่ลูกหนี้-เจ้าหนี้ให้จำนวนการโอนน้อยสุด (greedy) คืน list (จาก, ถึง, ยอด)."""
    names = set(paid) | set(owed)
    creditors, debtors = [], []
    for n in names:
        net = paid.get(n, 0.0) - owed.get(n, 0.0)
        if net > 0.01:
            creditors.append([n, net])
        elif net < -0.01:
            debtors.append([n, -net])
    creditors.sort(key=lambda x: -x[1])
    debtors.sort(key=lambda x: -x[1])
    transfers = []
    i = j = 0
    while i < len(debtors) and j < len(creditors):
        pay = min(debtors[i][1], creditors[j][1])
        transfers.append((debtors[i][0], creditors[j][0], pay))
        debtors[i][1] -= pay
        creditors[j][1] -= pay
        if debtors[i][1] < 0.01:
            i += 1
        if creditors[j][1] < 0.01:
            j += 1
    return transfers


def thai_dt_text(value):
    if not value:
        return "", ""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00")) + timedelta(hours=7)
        return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S")
    except Exception:
        s = str(value)
        return s[:10], s[11:19]


def push_line(token, target_id, message):
    if not token or not target_id:
        return
    res = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"to": target_id, "messages": [{"type": "text", "text": message[:4900]}]},
        timeout=20,
    )
    res.raise_for_status()


def update_job(supabase, job_id, payload):
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    supabase.table("export_jobs").update(payload).eq("id", job_id).execute()


def main():
    job_id = require_env("EXPORT_JOB_ID")
    supabase = create_client(require_env("SUPABASE_URL"), require_env("SUPABASE_KEY"))
    line_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")

    job_rows = supabase.table("export_jobs").select("*").eq("id", job_id).limit(1).execute().data or []
    if not job_rows:
        raise RuntimeError(f"export job not found: {job_id}")
    job = job_rows[0]
    target_id = job.get("target_id")

    try:
        update_job(supabase, job_id, {"status": "running", "error": None})
        trip_id = job.get("trip_id")
        trip_rows = supabase.table("trips").select("*").eq("id", trip_id).limit(1).execute().data or []
        if not trip_rows:
            raise RuntimeError("trip not found")
        trip = trip_rows[0]
        expenses = supabase.table("expenses").select("*").eq("trip_id", trip_id).order("id", desc=False).execute().data or []
        if not expenses:
            raise RuntimeError("ไม่มีข้อมูลค่าใช้จ่ายในทริปนี้")

        rows = []
        currency_totals = {}  # สกุล -> {"orig": ยอดสกุลเดิม, "thb": ยอดเทียบบาท, "rate": เรท}
        grand_thb = 0.0
        paid_by_person = {}   # ผู้จ่าย -> ยอดบาทที่ออกจริง
        owed_by_person = {}   # คน -> ยอดบาทที่ต้องจ่าย (ส่วนแบ่ง)
        for exp in expenses:
            date_text, time_text = thai_dt_text(exp.get("created_at"))
            participants = exp.get("participants") or []
            if isinstance(participants, str):
                participants = [p.strip() for p in participants.split() if p.strip()]
            currency = normalize_currency(exp.get("currency"))
            amount = as_float(exp.get("amount")) or 0.0
            amount_thb, rate, source = compute_thb(exp.get("amount"), currency)
            agg = currency_totals.setdefault(currency, {"orig": 0.0, "thb": 0.0, "rate": rate})
            agg["orig"] += amount
            agg["thb"] += amount_thb
            agg["rate"] = rate
            grand_thb += amount_thb
            payer = exp.get("payer_name") or exp.get("line_user_id") or ""
            if payer:
                paid_by_person[payer] = paid_by_person.get(payer, 0.0) + amount_thb
            share = amount_thb / max(len(participants), 1)
            for p in participants:
                owed_by_person[p] = owed_by_person.get(p, 0.0) + share
            rows.append({
                "ชื่อทริป": trip.get("title", ""),
                "วันที่": date_text,
                "เวลา": time_text,
                "ชื่อผู้จ่าย": exp.get("payer_name") or exp.get("line_user_id") or "",
                "รายการ": exp.get("item_name", ""),
                "จำนวนเงิน": amount,
                "สกุล": currency,
                "ยอดเทียบบาท": round(amount_thb, 2),
                "เรทที่ใช้": rate,
                "ที่มาเรท": source,
                "หมวดหมู่": exp.get("tag", ""),
                "หาร": " ".join(map(str, participants)),
            })

        df = pd.DataFrame(rows)

        # ตารางสรุปแปลงเป็นบาทด้านท้าย
        summary_rows = []
        for curr, agg in currency_totals.items():
            summary_rows.append({
                "สกุล": curr,
                "ยอดรวม (สกุลเดิม)": round(agg["orig"], 2),
                "เรท": agg["rate"],
                "ยอดรวมเทียบบาท": round(agg["thb"], 2),
            })
        summary_rows.append({
            "สกุล": "รวมทั้งหมด (บาท)",
            "ยอดรวม (สกุลเดิม)": "",
            "เรท": "",
            "ยอดรวมเทียบบาท": round(grand_thb, 2),
        })
        summary_df = pd.DataFrame(summary_rows)

        # ตาราง "จ่ายไปแล้ว" (ใครออกเงินจริงเท่าไร)
        paid_df = pd.DataFrame(
            [{"คน": p, "จ่ายไปแล้ว (บาท)": round(v, 2)} for p, v in sorted(paid_by_person.items())]
        )

        # ตาราง "สรุปโอนเงิน" (ใครต้องโอนให้ใคร)
        transfers = compute_settlement(paid_by_person, owed_by_person)
        if transfers:
            settle_df = pd.DataFrame(
                [{"จาก": f, "ถึง": t, "ยอดโอน (บาท)": round(amt, 2)} for f, t, amt in transfers]
            )
        else:
            settle_df = pd.DataFrame([{"จาก": "ไม่มียอดต้องโอน", "ถึง": "", "ยอดโอน (บาท)": ""}])

        filename = f"trip_{trip_id}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.xlsx"
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp_path = tmp.name

        def write_block(writer, title, block_df, start):
            pd.DataFrame([{"_": title}]).to_excel(
                writer, index=False, header=False, sheet_name="Expenses", startrow=start, startcol=0
            )
            block_df.to_excel(writer, index=False, sheet_name="Expenses", startrow=start + 1)
            return start + 1 + len(block_df) + 1 + 1  # หัวข้อ + header + แถว + เว้น 1 บรรทัด

        with pd.ExcelWriter(tmp_path, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Expenses")
            ptr = len(df) + 2  # เว้น 1 บรรทัดหลังตารางหลัก
            ptr = write_block(writer, "สรุปแปลงเป็นบาท", summary_df, ptr)
            if not paid_df.empty:
                ptr = write_block(writer, "จ่ายไปแล้ว", paid_df, ptr)
            write_block(writer, "สรุปโอนเงิน", settle_df, ptr)

        with open(tmp_path, "rb") as f:
            supabase.storage.from_("trip-exports").upload(
                path=filename,
                file=f.read(),
                file_options={"content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "upsert": "true"},
            )
        public_url = supabase.storage.from_("trip-exports").get_public_url(filename)
        update_job(supabase, job_id, {"status": "completed", "file_path": filename, "public_url": public_url})
        push_line(line_token, target_id, f"Excel สำเร็จ\nทริป: {trip.get('title', '-')}\n{public_url}")
    except Exception as exc:
        update_job(supabase, job_id, {"status": "failed", "error": str(exc)})
        push_line(line_token, target_id, f"Export Excel ไม่สำเร็จ\n{exc}")
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
