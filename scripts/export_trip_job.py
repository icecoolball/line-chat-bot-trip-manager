import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

import pandas as pd
import requests
from supabase import create_client


# เรทเทียบบาทแบบคงที่ (fallback สุดท้ายถ้าดึงเรทเรียลไทม์ไม่ได้) — ตรงกับ src/worker.ts
CURRENCY_RATES = {"THB": 1.0, "JPY": 0.23, "USD": 34.5, "KRW": 0.025}

# cache เรทเรียลไทม์ต่อการรัน 1 ครั้ง: สกุล -> เรทเทียบบาท (None = ดึงไม่ได้)
_FX_CACHE = {}


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
    curr = (currency or "THB").upper()
    amount_val = as_float(amount) or 0.0
    if curr == "THB":
        return round(amount_val, 2), 1.0, "same_currency"
    live_rate = fetch_fx_rate_to_thb(curr)
    if live_rate:
        return round(amount_val * live_rate, 2), live_rate, "er-api_export"
    rate = CURRENCY_RATES.get(curr, 1.0)
    return round(amount_val * rate, 2), rate, "fallback_export"


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
        for exp in expenses:
            date_text, time_text = thai_dt_text(exp.get("created_at"))
            participants = exp.get("participants") or []
            if isinstance(participants, str):
                participants = [p.strip() for p in participants.split() if p.strip()]
            currency = exp.get("currency", "THB") or "THB"
            amount = as_float(exp.get("amount")) or 0.0
            amount_thb, rate, source = compute_thb(exp.get("amount"), currency)
            agg = currency_totals.setdefault(currency, {"orig": 0.0, "thb": 0.0, "rate": rate})
            agg["orig"] += amount
            agg["thb"] += amount_thb
            agg["rate"] = rate
            grand_thb += amount_thb
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

        filename = f"trip_{trip_id}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.xlsx"
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp_path = tmp.name
        with pd.ExcelWriter(tmp_path, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Expenses")
            start_row = len(df) + 2  # เว้น 1 บรรทัดหลังตารางหลัก
            header_df = pd.DataFrame([{"สกุล": "สรุปแปลงเป็นบาท"}])
            header_df.to_excel(writer, index=False, header=False, sheet_name="Expenses", startrow=start_row, startcol=0)
            summary_df.to_excel(writer, index=False, sheet_name="Expenses", startrow=start_row + 1)

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
