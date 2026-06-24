import "jsr:@supabase/functions-js/edge-runtime.d.ts";

type ReminderJob = {
  reminder_id: number;
  schedule_id: string;
  event_name: string;
  event_site: string;
  event_url: string;
  sale_at: string;
  offset_minutes: number;
  attempt_count: number;
};

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const dbHeaders = {
  apikey: SERVICE_KEY,
  Authorization: `Bearer ${SERVICE_KEY}`,
  "Content-Type": "application/json",
};

async function loadLineConfig(): Promise<{ channel_access_token: string; target_id: string }> {
  const response = await fetch(`${SUPABASE_URL}/rest/v1/rpc/get_ticket_line_config`, {
    method: "POST",
    headers: dbHeaders,
    body: "{}",
  });
  if (!response.ok) throw new Error(`load LINE config: ${response.status} ${await response.text()}`);
  const rows = await response.json();
  const config = rows?.[0];
  if (!config?.channel_access_token || !config?.target_id) throw new Error("LINE config is missing from Supabase Vault");
  return config;
}

function labelForOffset(minutes: number): string {
  if (minutes === 1440) return "1 วัน";
  if (minutes === 60) return "1 ชั่วโมง";
  return `${minutes} นาที`;
}

function formatMessage(job: ReminderJob): string {
  const saleAt = new Intl.DateTimeFormat("th-TH", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Bangkok",
  }).format(new Date(job.sale_at));
  return `🔔 อีก ${labelForOffset(job.offset_minutes)} จะเปิดขายบัตร\n\n${job.event_name}\nเว็บไซต์: ${job.event_site}\nเวลา: ${saleAt}\n${job.event_url}`;
}

async function claimJobs(): Promise<ReminderJob[]> {
  const response = await fetch(`${SUPABASE_URL}/rest/v1/rpc/claim_ticket_reminders`, {
    method: "POST",
    headers: dbHeaders,
    body: JSON.stringify({ p_limit: 20 }),
  });
  if (!response.ok) throw new Error(`claim reminders: ${response.status} ${await response.text()}`);
  return await response.json();
}

async function updateReminder(id: number, values: Record<string, unknown>): Promise<void> {
  const response = await fetch(`${SUPABASE_URL}/rest/v1/schedule_reminders?id=eq.${id}`, {
    method: "PATCH",
    headers: { ...dbHeaders, Prefer: "return=minimal" },
    body: JSON.stringify(values),
  });
  if (!response.ok) throw new Error(`update reminder ${id}: ${response.status} ${await response.text()}`);
}

async function sendLine(job: ReminderJob, lineConfig: { channel_access_token: string; target_id: string }): Promise<void> {
  const response = await fetch("https://api.line.me/v2/bot/message/push", {
    method: "POST",
    headers: { Authorization: `Bearer ${lineConfig.channel_access_token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ to: lineConfig.target_id, messages: [{ type: "text", text: formatMessage(job) }] }),
  });
  if (!response.ok) throw new Error(`LINE push: ${response.status} ${await response.text()}`);
}

Deno.serve(async (request) => {
  if (request.method !== "POST") return new Response("Method not allowed", { status: 405 });
  if (!SUPABASE_URL || !SERVICE_KEY) {
    return Response.json({ ok: false, error: "Missing required function secrets" }, { status: 500 });
  }

  try {
    const lineConfig = await loadLineConfig();
    const jobs = await claimJobs();
    let sent = 0;
    let failed = 0;
    for (const job of jobs) {
      try {
        await sendLine(job, lineConfig);
        await updateReminder(job.reminder_id, {
          status: "sent",
          sent_at: new Date().toISOString(),
          claimed_at: null,
          last_error: null,
        });
        sent += 1;
      } catch (error) {
        const retryMinutes = Math.min(10, 2 ** Math.max(0, job.attempt_count - 1));
        await updateReminder(job.reminder_id, {
          status: job.attempt_count >= 5 ? "skipped" : "failed",
          next_attempt_at: new Date(Date.now() + retryMinutes * 60_000).toISOString(),
          claimed_at: null,
          last_error: String(error instanceof Error ? error.message : error).slice(0, 500),
        });
        failed += 1;
      }
    }
    return Response.json({ ok: true, claimed: jobs.length, sent, failed });
  } catch (error) {
    console.error(error);
    return Response.json({ ok: false, error: String(error instanceof Error ? error.message : error) }, { status: 500 });
  }
});
