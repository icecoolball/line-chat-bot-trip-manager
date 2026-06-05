import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

import pandas as pd
import requests
from supabase import create_client


def require_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing {name}")
    return value


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
        for exp in expenses:
            date_text, time_text = thai_dt_text(exp.get("created_at"))
            participants = exp.get("participants") or []
            if isinstance(participants, str):
                participants = [p.strip() for p in participants.split() if p.strip()]
            rows.append({
                "ชื่อทริป": trip.get("title", ""),
                "วันที่": date_text,
                "เวลา": time_text,
                "ชื่อผู้จ่าย": exp.get("payer_name") or exp.get("line_user_id") or "",
                "รายการ": exp.get("item_name", ""),
                "จำนวนเงิน": exp.get("amount", 0),
                "สกุล": exp.get("currency", "THB"),
                "ยอดเทียบบาท": exp.get("amount_thb", ""),
                "เรทที่ใช้": exp.get("exchange_rate_used", ""),
                "ที่มาเรท": exp.get("exchange_rate_source", ""),
                "หมวดหมู่": exp.get("tag", ""),
                "หาร": " ".join(map(str, participants)),
            })

        filename = f"trip_{trip_id}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.xlsx"
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            pd.DataFrame(rows).to_excel(tmp.name, index=False, sheet_name="Expenses")
            tmp_path = tmp.name

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
