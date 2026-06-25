import "jsr:@supabase/functions-js/edge-runtime.d.ts";

type QueueMessage = {
  msg_id: number;
  read_ct: number;
  enqueued_at: string;
  vt: string;
  message: { reminder_id?: number };
};

type ReminderJob = {
  reminder_id: number;
  schedule_id: string;
  event_name: string;
  event_site: string;
  event_url: string;
  sale_at: string;
  offset_minutes: number;
  attempt_count: number;
  status: string;
};

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const QUEUE_VISIBILITY_SECONDS = 120;
const MAX_ATTEMPTS = 5;
const dbHeaders = {
  apikey: SERVICE_KEY,
  Authorization: `Bearer ${SERVICE_KEY}`,
  "Content-Type": "application/json",
};

async function rpc<T>(name: string, body: Record<string, unknown> = {}): Promise<T> {
  const response = await fetch(`${SUPABASE_URL}/rest/v1/rpc/${name}`, {
    method: "POST",
    headers: dbHeaders,
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`${name}: ${response.status} ${await response.text()}`);
  return await response.json();
}

async function loadLineConfig(): Promise<{ channel_access_token: string; target_id: string }> {
  const rows = await rpc<Array<{ channel_access_token: string; target_id: string }>>("get_ticket_line_config");
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

async function readQueue(limit: number): Promise<QueueMessage[]> {
  return await rpc<QueueMessage[]>("ticket_read_reminder_queue", {
    p_limit: limit,
    p_visibility_seconds: QUEUE_VISIBILITY_SECONDS,
  });
}

async function deleteQueueMessage(messageId: number): Promise<void> {
  await rpc("ticket_delete_reminder_queue_message", { p_message_id: messageId });
}

async function loadReminderJob(reminderId: number): Promise<ReminderJob | null> {
  const rows = await rpc<ReminderJob[]>("ticket_get_reminder_job", { p_reminder_id: reminderId });
  return rows?.[0] || null;
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
    headers: {
      Authorization: `Bearer ${lineConfig.channel_access_token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      to: lineConfig.target_id,
      messages: [{ type: "text", text: formatMessage(job) }],
    }),
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
    const messages = await readQueue(20);
    let sent = 0;
    let failed = 0;

    for (const queueMessage of messages) {
      const reminderId = Number(queueMessage?.message?.reminder_id);
      if (!Number.isFinite(reminderId)) {
        await deleteQueueMessage(queueMessage.msg_id);
        continue;
      }

      const job = await loadReminderJob(reminderId);
      if (!job || job.status === "sent" || job.status === "skipped") {
        await deleteQueueMessage(queueMessage.msg_id);
        continue;
      }

      try {
        await updateReminder(job.reminder_id, {
          status: "processing",
          claimed_at: new Date().toISOString(),
          attempt_count: Math.max(job.attempt_count, queueMessage.read_ct),
        });
        await sendLine(job, lineConfig);
        await updateReminder(job.reminder_id, {
          status: "sent",
          sent_at: new Date().toISOString(),
          claimed_at: null,
          last_error: null,
          attempt_count: Math.max(job.attempt_count, queueMessage.read_ct),
        });
        await deleteQueueMessage(queueMessage.msg_id);
        sent += 1;
      } catch (error) {
        const message = String(error instanceof Error ? error.message : error).slice(0, 500);
        const terminal = queueMessage.read_ct >= MAX_ATTEMPTS;
        await updateReminder(job.reminder_id, {
          status: terminal ? "skipped" : "failed",
          claimed_at: null,
          last_error: message,
          attempt_count: Math.max(job.attempt_count, queueMessage.read_ct),
          next_attempt_at: new Date(Date.now() + QUEUE_VISIBILITY_SECONDS * 1000).toISOString(),
        });
        if (terminal) {
          await deleteQueueMessage(queueMessage.msg_id);
        }
        failed += 1;
      }
    }

    return Response.json({ ok: true, claimed: messages.length, sent, failed });
  } catch (error) {
    console.error(error);
    return Response.json({ ok: false, error: String(error instanceof Error ? error.message : error) }, { status: 500 });
  }
});
