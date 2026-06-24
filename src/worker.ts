import {
  COUNTRY_TO_CURRENCY,
  ISO_4217,
  FALLBACK_RATES,
  normalizeCountryName,
  normalizeCurrencyCode,
} from "./currency-by-country";

type Env = {
  LINE_CHANNEL_ACCESS_TOKEN: string;
  LINE_CHANNEL_SECRET: string;
  SUPABASE_URL: string;
  SUPABASE_SERVICE_ROLE_KEY?: string;
  SUPABASE_KEY?: string;
  SUPABASE_ANON_KEY?: string;
  OCR_SPACE_API_KEY?: string;
  CRON_SECRET?: string;
  GITHUB_TOKEN?: string;
  GITHUB_REPO?: string;
  GITHUB_WORKFLOW_ID?: string;
  GITHUB_WORKFLOW_REF?: string;
};

type BotState = {
  user_id: string;
  group_id?: string | null;
  action: string;
  payload: Record<string, unknown>;
  expires_at?: string | null;
};

type Trip = Record<string, any>;
type Expense = Record<string, any>;

const STATE_TIMEOUT_SECONDS = 600;
const SHOWTIME_ACTIONS = new Set([
  "showtime_mode",
  "wait_showtime_event_name",
  "wait_showtime_date",
  "wait_end_showtime_event_index",
  "wait_end_showtime_confirm",
]);

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    try {
      if (url.pathname === "/callback" && request.method === "POST") return handleLineCallback(request, env, ctx);
      if (url.pathname === "/api/config-status" && request.method === "GET") {
        return json({ ok: true, lineTokenConfigured: !!env.LINE_CHANNEL_ACCESS_TOKEN, lineSecretConfigured: !!env.LINE_CHANNEL_SECRET });
      }
      if (url.pathname === "/api/line-push" && request.method === "POST") return handleLinePush(request, env);
      if (url.pathname === "/api/schedules") return handleSchedules(request, env);
      if (url.pathname.startsWith("/api/schedules/") && request.method === "DELETE") return deleteSchedule(request, env);
      if (url.pathname === "/api/check-showtime" && request.method === "POST") return authCron(request, env, () => runShowtimeCheck(env));
      if (url.pathname === "/api/daily-summary" && request.method === "POST") return authCron(request, env, () => runDailySummary(env));
      if (url.pathname === "/api/export-trip" && request.method === "POST") return handleExportTrip(request, env, ctx);
      if (url.pathname === "/api/server-time" && request.method === "GET") return json({ ok: true, serverTime: Date.now() });
      return json({ ok: false, error: "Not found" }, 404);
    } catch (error) {
      return json({ ok: false, error: errorMessage(error) }, 500);
    }
  },

  async scheduled(event: ScheduledEvent, env: Env): Promise<void> {
    if (event.cron === "0 2 * * *") await runDailySummary(env);
    else await runShowtimeCheck(env);
    await deleteExpiredStates(env);
  },
};

async function handleLineCallback(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
  const signature = request.headers.get("X-Line-Signature") || "";
  const body = await request.text();
  if (!(await verifyLineSignature(body, signature, env.LINE_CHANNEL_SECRET))) return new Response("Invalid signature", { status: 400 });
  const payload = JSON.parse(body);
  for (const event of payload.events || []) {
    try {
      await handleLineEvent(event, env, ctx);
    } catch (error) {
      console.error("LINE event failed", errorMessage(error));
      const replyToken = event?.replyToken;
      if (replyToken) {
        try {
          await reply(env, replyToken, `เกิด error: ${errorMessage(error).slice(0, 300)}`);
        } catch (replyError) {
          console.error("LINE error reply failed", errorMessage(replyError));
        }
      }
    }
  }
  return new Response("OK");
}

async function handleLineEvent(event: any, env: Env, ctx: ExecutionContext): Promise<void> {
  const source = event.source || {};
  const userId = source.userId;
  const groupId = source.groupId || null;
  const targetId = groupId || userId;
  if (!userId || !event.replyToken) return;

  if (event.type === "postback") {
    await handlePostback(event, userId, groupId, event.replyToken, env);
    return;
  }
  if (event.type !== "message") return;

  if (event.message?.type === "text") {
    await handleText(event.message.text || "", userId, groupId, targetId, event.replyToken, env, ctx);
    return;
  }
  if (event.message?.type === "image") {
    await handleImage(event.message.id, userId, groupId, targetId, event.replyToken, env, ctx);
  }
}

async function handleText(rawText: string, userId: string, groupId: string | null, targetId: string, replyToken: string, env: Env, ctx: ExecutionContext): Promise<void> {
  const text = rawText.trim();
  const lower = text.toLowerCase();
  const state = await getState(env, userId);

  if (lower === "state") return reply(env, replyToken, buildStateText(state));
  if (["menu", "เมนู"].includes(lower)) return replyFlex(env, replyToken, buildMainMenuFlex(), QR_MAIN);
  if (["help", "ช่วยเหลือ"].includes(lower)) return replyFlex(env, replyToken, buildHelpFlex(), QR_MAIN);
  if (state && SHOWTIME_ACTIONS.has(state.action) && lower.startsWith("edit showtime")) return handleShowtimeText(text, userId, groupId, targetId, replyToken, state, env);
  if (lower === "edit" || lower.startsWith("edit ")) return handleEditExpense(text, userId, groupId, replyToken, env);
  if (state?.action === "export_history" && ["cancel", "exit", "ออก", "ยกเลิก"].includes(lower)) {
    await clearState(env, userId);
    return reply(env, replyToken, "ออกจาก history แล้ว");
  }
  if (lower === "menu showtime" || lower === "help showtime") {
    if (state && SHOWTIME_ACTIONS.has(state.action)) return replyFlex(env, replyToken, buildShowtimeMenuFlex());
    return reply(env, replyToken, "ใช้ menu/help showtime ได้เฉพาะตอนอยู่ในโหมด Showtime");
  }
  if (/^showtime\s+\d+$/i.test(lower)) return handleShowtimeNumber(text, replyToken, env);
  if (lower === "end showtime" || /^end\s+showtime\s+\d+$/i.test(lower)) return handleEndShowtimeCommand(text, userId, groupId, replyToken, env);

  if (text === "ทริป" || lower === "trip") {
    await setState(env, userId, groupId, "wait_trip_name", {});
    return reply(env, replyToken, "กรุณาระบุชื่อทริป", QR_CANCEL);
  }
  if (lower.startsWith("trip ") || text.startsWith("ทริป ")) {
    const name = text.replace(/^trip\s+/i, "").replace(/^ทริป\s+/, "").trim();
    await setState(env, userId, groupId, "wait_trip_currency", { trip_name: name });
    return reply(env, replyToken, "ระบุประเทศของทริป เช่น ญี่ปุ่น / เกาหลี หรือพิมพ์รหัสสกุลเงิน เช่น JPY", QR_COUNTRY);
  }

  if (state?.action === "wait_slip_confirm") return handleSlipConfirm(text, userId, replyToken, state, env);
  if (state?.action === "wait_slip_amount") return handleSlipManualAmount(text, userId, replyToken, state, env);
  if (state?.action === "wait_slip_checking") return reply(env, replyToken, "กำลังตรวจยอดจากสลิปอยู่ รอสักครู่");
  if (state?.action === "wait_slip_payer") return handleSlipAssignment(text, userId, groupId, replyToken, state, env, ctx);
  if (state?.action === "wait_trip_name") return handleTripName(text, userId, groupId, replyToken, env);
  if (state?.action === "wait_trip_currency") return handleTripCurrency(text, userId, groupId, replyToken, state, env);
  if (state?.action === "wait_trip_start_date") return handleTripStartDateText(text, userId, groupId, replyToken, state, env);
  if (state?.action === "wait_trip_end_date") return handleTripEndDateText(text, userId, groupId, replyToken, state, env);
  if (state?.action === "wait_trip_dates") return handleTripDates(text, userId, groupId, replyToken, state, env);
  if (state?.action === "wait_end_trip_confirm") return handleEndTripConfirm(text, userId, groupId, replyToken, state, env);
  if (state && SHOWTIME_ACTIONS.has(state.action)) return handleShowtimeText(text, userId, groupId, targetId, replyToken, state, env);
  if (state?.action === "export_history" && ["history", "ประวัติ"].includes(lower)) return handleHistory(userId, groupId, targetId, replyToken, env);
  if (state?.action === "export_history") return handleExportHistoryChoice(text, userId, targetId, replyToken, state, env, ctx);

  if (["ยอด", "sum"].includes(lower)) return replyAuto(env, replyToken, await buildTripTotalMessage(env, userId, groupId), QR_MAIN);
  if (["ยอดวันนี้", "today"].includes(lower)) return replyAuto(env, replyToken, await buildTodayMessage(env, userId, groupId), QR_MAIN);
  if (lower.startsWith("edit ")) return handleEditExpense(text, userId, groupId, replyToken, env);
  if (["history", "ประวัติ"].includes(lower)) return handleHistory(userId, groupId, targetId, replyToken, env);
  if (lower.startsWith("excel")) return handleExportCommand(userId, groupId, targetId, replyToken, env, ctx);
  if (["end trip", "จบทริป"].includes(lower)) return handleEndTrip(userId, groupId, replyToken, env);
  if (lower === "showtime" || lower === "เพิ่ม" || lower === "add showtime") return enterShowtime(text, userId, groupId, targetId, replyToken, env);

  const trip = await getActiveTrip(env, userId, groupId);
  const parsed = parseExpense(text, await getDisplayName(env, userId, groupId), getTripBaseCurrency(trip));
  if (trip && parsed) {
    const nameWarnings = await findSimilarPeopleNames(env, trip.id, parsed.participants);
    if (nameWarnings.length) return reply(env, replyToken, buildSimilarNameWarning(nameWarnings));
    const saved = await saveExpense(env, trip.id, userId, parsed.payer, parsed.item, parsed.amount, parsed.currency, parsed.tag, parsed.participants);
    return replyAuto(env, replyToken, buildSaveCard({ amount: parsed.amount, currency: parsed.currency, tag: parsed.tag, people: parsed.participants, payer: parsed.payer, id: saved?.id }), QR_MAIN);
  }

  return reply(env, replyToken, "⚠️ ไม่เข้าใจคำสั่ง พิมพ์ help เพื่อดูคำสั่งทั้งหมด", trip ? QR_MAIN : QR_NOTRIP);
}

async function handleTripName(text: string, userId: string, groupId: string | null, replyToken: string, env: Env): Promise<void> {
  if (["cancel", "exit", "ยกเลิก", "ออก"].includes(text.toLowerCase())) {
    await clearState(env, userId);
    return reply(env, replyToken, "ยกเลิกการสร้างทริปแล้ว");
  }
  if (!text) return reply(env, replyToken, "กรุณาระบุชื่อทริป", QR_CANCEL);
  await setState(env, userId, groupId, "wait_trip_currency", { trip_name: text });
  return reply(env, replyToken, "ระบุประเทศของทริป เช่น ญี่ปุ่น / เกาหลี หรือพิมพ์รหัสสกุลเงิน เช่น JPY", QR_COUNTRY);
}

async function handleTripCurrency(text: string, userId: string, groupId: string | null, replyToken: string, state: BotState, env: Env): Promise<void> {
  const currency = resolveBaseCurrency(text);
  if (!currency) return reply(env, replyToken, "⚠️ ไม่รู้จักประเทศ/สกุลเงินนี้ ลองพิมพ์ชื่อประเทศ เช่น ญี่ปุ่น หรือรหัสสกุล เช่น JPY", QR_COUNTRY);
  const tripName = String(state.payload.trip_name || "").trim();
  await setState(env, userId, groupId, "wait_trip_start_date", { trip_name: tripName, base_currency: currency });
  return replyMessages(env, replyToken, [buildTripDatePickerMessage("start")]);
}

async function handlePostback(event: any, userId: string, groupId: string | null, replyToken: string, env: Env): Promise<void> {
  const data = new URLSearchParams(String(event.postback?.data || ""));
  const stage = data.get("trip_date");
  if (stage !== "start" && stage !== "end") return;

  const selectedDate = String(event.postback?.params?.date || "");
  const parsed = parseTripDates(selectedDate);
  if (!parsed?.start) return reply(env, replyToken, "⚠️ วันที่ไม่ถูกต้อง กรุณาเลือกใหม่");

  const state = await getState(env, userId);
  if (stage === "start" && state?.action === "wait_trip_start_date") {
    await setState(env, userId, groupId, "wait_trip_end_date", { ...state.payload, start_date: parsed.start });
    return replyMessages(env, replyToken, [buildTripDatePickerMessage("end", { initial: parsed.start, min: parsed.start })]);
  }
  if (stage === "end" && state?.action === "wait_trip_end_date") {
    const start = String(state.payload.start_date || "");
    if (parsed.start < start) {
      return replyMessages(env, replyToken, [buildTripDatePickerMessage("end", { initial: start, min: start, error: "วันสิ้นสุดต้องไม่ก่อนวันเริ่ม" })]);
    }
    return createTripFromDates(userId, groupId, replyToken, state, { start, end: parsed.start }, env);
  }
  return reply(env, replyToken, "ขั้นตอนเลือกวันที่หมดอายุแล้ว กรุณาพิมพ์ ทริป เพื่อเริ่มใหม่");
}

async function handleTripStartDateText(text: string, userId: string, groupId: string | null, replyToken: string, state: BotState, env: Env): Promise<void> {
  const lower = text.trim().toLowerCase();
  if (["cancel", "exit", "ยกเลิก", "ออก"].includes(lower)) {
    await clearState(env, userId);
    return reply(env, replyToken, "ยกเลิกการสร้างทริปแล้ว");
  }
  if (["ข้าม", "skip", "ไม่ระบุ", "none", "-"].includes(lower)) {
    return createTripFromDates(userId, groupId, replyToken, state, { start: null, end: null }, env);
  }
  const dates = parseTripDates(text);
  if (!dates?.start) return replyMessages(env, replyToken, [buildTripDatePickerMessage("start", { error: "อ่านวันเริ่มไม่ออก กรุณาเลือกจากปฏิทิน" })]);
  if (dates.end) return createTripFromDates(userId, groupId, replyToken, state, dates, env);
  await setState(env, userId, groupId, "wait_trip_end_date", { ...state.payload, start_date: dates.start });
  return replyMessages(env, replyToken, [buildTripDatePickerMessage("end", { initial: dates.start, min: dates.start })]);
}

async function handleTripEndDateText(text: string, userId: string, groupId: string | null, replyToken: string, state: BotState, env: Env): Promise<void> {
  const lower = text.trim().toLowerCase();
  if (["cancel", "exit", "ยกเลิก", "ออก"].includes(lower)) {
    await clearState(env, userId);
    return reply(env, replyToken, "ยกเลิกการสร้างทริปแล้ว");
  }
  const start = String(state.payload.start_date || "");
  if (["วันเดียว", "ข้าม", "skip", "ไม่ระบุ", "none", "-"].includes(lower)) {
    return createTripFromDates(userId, groupId, replyToken, state, { start, end: null }, env);
  }
  const dates = parseTripDates(text);
  const end = dates?.start;
  if (!end || end < start) {
    return replyMessages(env, replyToken, [buildTripDatePickerMessage("end", { initial: start, min: start, error: "วันสิ้นสุดต้องไม่ก่อนวันเริ่ม" })]);
  }
  return createTripFromDates(userId, groupId, replyToken, state, { start, end }, env);
}

export function buildTripDatePickerMessage(stage: "start" | "end", options: { initial?: string; min?: string; error?: string } = {}): Record<string, unknown> {
  const isStart = stage === "start";
  const action: Record<string, unknown> = {
    type: "datetimepicker",
    label: isStart ? "เลือกวันเริ่ม" : "เลือกวันสิ้นสุด",
    data: `trip_date=${stage}`,
    mode: "date",
  };
  if (options.initial) action.initial = options.initial;
  if (options.min) action.min = options.min;
  return {
    type: "text",
    text: options.error || (isStart ? "เลือกวันเริ่มทริปจากปฏิทิน" : "เลือกวันสิ้นสุดทริปจากปฏิทิน"),
    quickReply: {
      items: [
        { type: "action", action },
        { type: "action", action: { type: "message", label: isStart ? "ไม่ระบุวันที่" : "ทริปวันเดียว", text: isStart ? "ข้าม" : "วันเดียว" } },
      ],
    },
  };
}

async function handleTripDates(text: string, userId: string, groupId: string | null, replyToken: string, state: BotState, env: Env): Promise<void> {
  if (["cancel", "exit", "ยกเลิก", "ออก"].includes(text.trim().toLowerCase())) {
    await clearState(env, userId);
    return reply(env, replyToken, "ยกเลิกการสร้างทริปแล้ว");
  }
  const dates = parseTripDates(text);
  if (!dates) return reply(env, replyToken, "อ่านวันที่ไม่ออก ลองพิมพ์ เช่น 23/06/2026-27/06/2026 หรือพิมพ์ ข้าม");
  return createTripFromDates(userId, groupId, replyToken, state, dates, env);
}

async function createTripFromDates(userId: string, groupId: string | null, replyToken: string, state: BotState, dates: { start: string | null; end: string | null }, env: Env): Promise<void> {
  const tripName = String(state.payload.trip_name || "").trim();
  const currency = String(state.payload.base_currency || "THB");
  await supabasePatch(env, "trips", { status: "closed" }, [`creator_id=eq.${encodeURIComponent(userId)}`]);
  await supabaseInsert(env, "trips", {
    title: tripName, status: "active", line_group_id: groupId, creator_id: userId,
    base_currency: currency, start_date: dates.start, end_date: dates.end,
  });
  await clearState(env, userId);
  let dateLine = "";
  if (dates.start) {
    const days = dates.end ? Math.round((Date.parse(dates.end) - Date.parse(dates.start)) / 86400000) + 1 : null;
    dateLine = `\nช่วง: ${dates.start}${dates.end ? ` ถึง ${dates.end}` : ""}${days ? ` (${days} วัน)` : ""}`;
  }
  return reply(env, replyToken, `เริ่มทริปใหม่: ${tripName}\nสกุลเงินหลัก: ${currency}${dateLine}`, QR_MAIN);
}

// คืน {start,end} (ISO) / {start:null,end:null} ถ้าข้าม / null ถ้าอ่านไม่ออก
export function parseTripDates(text: string): { start: string | null; end: string | null } | null {
  const t = String(text || "").trim();
  if (!t) return null;
  if (["ข้าม", "-", "skip", "ไม่ระบุ", "none"].includes(t.toLowerCase())) return { start: null, end: null };
  const found: string[] = [];
  const re = /(\d{1,4})[\/-](\d{1,2})[\/-](\d{1,4})/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(t)) !== null) {
    let a = Number(m[1]);
    const b = Number(m[2]);
    let c = Number(m[3]);
    let year: number;
    let month: number;
    let day: number;
    if (a > 31) { year = a; month = b; day = c; } // YYYY-MM-DD
    else { day = a; month = b; year = c; }         // DD/MM/YYYY
    if (year < 100) year += 2000;
    if (year > 2400) year -= 543; // พ.ศ. -> ค.ศ.
    if (month < 1 || month > 12 || day < 1 || day > 31) continue;
    found.push(`${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`);
  }
  if (!found.length) return null;
  return { start: found[0], end: found[1] || null };
}

async function handleImage(messageId: string, userId: string, groupId: string | null, targetId: string, replyToken: string, env: Env, ctx: ExecutionContext): Promise<void> {
  const state = await getState(env, userId);
  if (state && SHOWTIME_ACTIONS.has(state.action)) {
    waitUntilShowtimeImageOcr(ctx, env, messageId, userId, groupId, targetId);
    return reply(env, replyToken, "รับรูป Showtime แล้ว กำลังอ่านตาราง...");
  }

  const trip = await getActiveTrip(env, userId, groupId);
  if (!trip) return reply(env, replyToken, "ไม่มีทริปที่กำลังทำงานอยู่");
  const current = state?.action === "wait_slip_payer" ? ((state.payload.message_ids as string[]) || []) : [];
  current.push(messageId);
  await setState(env, userId, groupId, "wait_slip_payer", { message_ids: current, trip_id: trip.id, base_currency: getTripBaseCurrency(trip), target_id: targetId });
  return reply(env, replyToken, `รับสลิป/บิลแล้ว (${current.length} ใบ)\nพิมพ์ ผู้จ่าย #หมวด คนหาร...\nเช่น บอล #ค่าอาหาร บอล ปาค`, QR_CANCEL);
}

function waitUntilShowtimeImageOcr(ctx: ExecutionContext, env: Env, messageId: string, userId: string, groupId: string | null, targetId: string): void {
  ctx.waitUntil(finishShowtimeImageOcr(env, messageId, userId, groupId, targetId).catch((error) => console.error("Showtime OCR background failed", errorMessage(error))));
}

async function finishShowtimeImageOcr(env: Env, messageId: string, userId: string, groupId: string | null, targetId: string): Promise<void> {
  const text = await ocrLineImage(env, messageId);
  const items = extractShowtime(text || "");
  if (!items.length) {
    await push(env, targetId, "อ่านรูปแล้วแต่ไม่พบข้อมูล Showtime\nลองพิมพ์ตารางเอง เช่น 17:00-17:50 KLEAR");
    return;
  }
  const state = await getState(env, userId);
  const basePayload = state && SHOWTIME_ACTIONS.has(state.action) ? state.payload : {};
  const baseItems = (basePayload.showtime_temp as any[] || []).length
    ? (basePayload.showtime_temp as any[])
    : basePayload.event_name
      ? (await loadShowtime(env, String(basePayload.event_name))).schedule
      : [];
  const merged = mergeShowtimeItems(baseItems, items);
  await setState(env, userId, groupId, "showtime_mode", { ...basePayload, showtime_temp: merged, target_id: targetId });
  await push(env, targetId, "อ่าน Showtime สำเร็จ\n" + formatShowtimeItems(merged) + "\nพิมพ์ save เพื่อบันทึก");
}

async function handleSlipAssignment(text: string, userId: string, groupId: string | null, replyToken: string, state: BotState, env: Env, ctx: ExecutionContext): Promise<void> {
  const parsed = parseSlipAssignment(text);
  if (!parsed.participants.length) return reply(env, replyToken, "กรุณาพิมพ์ชื่ออย่างน้อย 1 คน");
  const tripId = String(state.payload.trip_id || "");
  const currency = parsed.currency || String(state.payload.base_currency || "THB");
  const nameWarnings = await findSimilarPeopleNames(env, tripId, parsed.participants);
  if (nameWarnings.length) return reply(env, replyToken, buildSimilarNameWarning(nameWarnings));
  const messageIds = Array.isArray(state.payload.message_ids) ? state.payload.message_ids.map(String) : [];
  const nextPayload = {
    trip_id: tripId,
    payer: parsed.payer,
    participants: parsed.participants,
    tag: parsed.tag || "#สลิป",
    currency,
    message_ids: messageIds,
    target_id: String(state.payload.target_id || groupId || userId),
  };
  await setState(env, userId, groupId, "wait_slip_checking", nextPayload);
  waitUntilSlipTotalCheck(ctx, env, userId, groupId, nextPayload);
  return reply(env, replyToken, `รับรายชื่อแล้ว กำลังตรวจยอดจากสลิป...\nถ้าระบบอ่านได้จะส่งยอดมาให้ยืนยัน\nถ้าอ่านไม่ได้จะให้พิมพ์ยอดเอง`);
}

function waitUntilSlipTotalCheck(ctx: ExecutionContext, env: Env, userId: string, groupId: string | null, payload: Record<string, unknown>): void {
  const task = finishSlipTotalCheck(env, userId, groupId, payload);
  ctx.waitUntil(task.catch((error) => console.error("Slip OCR background failed", errorMessage(error))));
}

async function finishSlipTotalCheck(env: Env, userId: string, groupId: string | null, payload: Record<string, unknown>): Promise<void> {
  const targetId = String(payload.target_id || groupId || userId);
  const currency = String(payload.currency || "THB");
  const messageIds = Array.isArray(payload.message_ids) ? payload.message_ids.map(String) : [];
  const slipAmounts = await detectSlipAmounts(env, messageIds);
  const step = getSlipReviewStep(slipAmounts, 0, currency);
  const nextPayload = { ...payload, slip_amounts: slipAmounts, slip_index: 0, slip_amount: step.amount, saved_slip_amounts: [], saved_slip_ids: [] };
  await setState(env, userId, groupId, step.action, nextPayload);
  await push(env, targetId, step.message);
}

async function handleSlipManualAmount(text: string, userId: string, replyToken: string, state: BotState, env: Env): Promise<void> {
  const amount = parsePositiveAmount(text);
  if (!amount) return reply(env, replyToken, "พิมพ์ยอดเป็นตัวเลข เช่น 120 หรือ 120.50");
  return saveSlipFromState(amount, userId, replyToken, state, env, "ตรวจยอดจากสลิปไม่ได้ บันทึกยอดที่พิมพ์");
}

async function handleSlipConfirm(text: string, userId: string, replyToken: string, state: BotState, env: Env): Promise<void> {
  const lower = text.trim().toLowerCase();
  const slipAmount = Number(state.payload.slip_amount || 0);
  if (["ใช่", "yes", "y", "ถูก"].includes(lower)) {
    return saveSlipFromState(slipAmount, userId, replyToken, state, env, "ใช่ ยอดตรงกับสลิป");
  }
  const override = text.match(/^ไม่\s+(\d+(?:\.\d{1,2})?)$/i) || text.match(/^no\s+(\d+(?:\.\d{1,2})?)$/i);
  if (override) {
    const amount = parsePositiveAmount(override[1]);
    if (amount) return saveSlipFromState(amount, userId, replyToken, state, env, `ไม่ ใช้ยอดที่แก้เป็น ${amount.toLocaleString()}`);
  }
  return reply(env, replyToken, `ตอบ "ใช่" เพื่อบันทึก ${slipAmount.toLocaleString()}\nหรือ "ไม่ [ยอดที่ถูก]" เช่น ไม่ 180`);
}

async function saveSlipFromState(amount: number, userId: string, replyToken: string, state: BotState, env: Env, prefix: string): Promise<void> {
  const tripId = String(state.payload.trip_id || "");
  const payer = String(state.payload.payer || "");
  const people = Array.isArray(state.payload.participants) ? state.payload.participants.map(String) : [];
  const tag = String(state.payload.tag || "#สลิป");
  const currency = String(state.payload.currency || "THB");
  const messageIds = Array.isArray(state.payload.message_ids) ? state.payload.message_ids.map(String) : [];
  const slipAmounts = Array.isArray(state.payload.slip_amounts) ? state.payload.slip_amounts.map((value) => value === null ? null : Number(value)) : [amount];
  const index = Math.max(0, Number(state.payload.slip_index || 0));
  const total = Math.max(messageIds.length, slipAmounts.length, 1);
  const label = `บิล ${index + 1}/${total} ${new Date().toLocaleString("th-TH", { timeZone: "Asia/Bangkok" })}`;
  const row = await saveExpense(env, tripId, userId, payer, label, amount, currency, tag, people, "manual_slip");
  const savedAmounts = Array.isArray(state.payload.saved_slip_amounts) ? state.payload.saved_slip_amounts.map(Number) : [];
  const savedIds = Array.isArray(state.payload.saved_slip_ids) ? state.payload.saved_slip_ids.map(String) : [];
  savedAmounts.push(amount);
  savedIds.push(row?.id !== undefined && row?.id !== null ? String(row.id) : "");

  const nextIndex = index + 1;
  if (nextIndex < total) {
    const step = getSlipReviewStep(slipAmounts, nextIndex, currency);
    await setState(env, userId, state.group_id || null, step.action, {
      ...state.payload,
      slip_index: nextIndex,
      slip_amount: step.amount,
      saved_slip_amounts: savedAmounts,
      saved_slip_ids: savedIds,
    });
    return reply(env, replyToken, `บันทึกใบที่ ${index + 1}/${total}: ${amount.toLocaleString()} ${currency}\n\n${step.message}`);
  }

  await clearState(env, userId);
  if (total === 1) return replyAuto(env, replyToken, buildSaveCard({ amount, currency, tag, people, payer, id: row?.id, prefix }));
  return reply(env, replyToken, buildSlipBatchSavedMessage(savedAmounts, savedIds, currency));
}

export function getSlipReviewStep(amounts: Array<number | null>, index: number, currency: string): { action: "wait_slip_confirm" | "wait_slip_amount"; amount: number | null; message: string } {
  const total = amounts.length;
  const amount = amounts[index] ?? null;
  const header = `ใบที่ ${index + 1}/${total}`;
  if (amount === null) {
    return { action: "wait_slip_amount", amount: null, message: `${header}: อ่านยอดไม่ได้\nพิมพ์ยอดของใบนี้ เช่น 120 หรือ 120.50` };
  }
  return {
    action: "wait_slip_confirm",
    amount,
    message: `${header}: ตรวจพบ ${amount.toLocaleString()} ${currency}\nถ้าถูกต้องพิมพ์: ใช่\nถ้าไม่ถูก พิมพ์: ไม่ [ยอดที่ถูก]\nเช่น ไม่ 180`,
  };
}

export function buildSlipBatchSavedMessage(amounts: number[], ids: string[], currency: string): string {
  const lines = amounts.map((amount, index) => `ใบที่ ${index + 1}: ${amount.toLocaleString()} ${currency}${ids[index] ? ` (ID ${ids[index]})` : ""}`);
  return `บันทึกครบ ${amounts.length} ใบ แยกเป็น ${amounts.length} รายการ\n${lines.join("\n")}`;
}

async function enterShowtime(text: string, userId: string, groupId: string | null, targetId: string, replyToken: string, env: Env): Promise<void> {
  const events = await listShowtimeEvents(env);
  if (text.toLowerCase() === "showtime" && events.length) {
    await setState(env, userId, groupId, "showtime_mode", { target_id: targetId, events, showtime_temp: [] });
    return reply(env, replyToken, buildShowtimeEventList(events) + "\nพิมพ์ menu showtime เพื่อดูคำสั่ง หรือ exit เพื่อออก");
  }
  await setState(env, userId, groupId, "wait_showtime_event_name", { target_id: targetId });
  return reply(env, replyToken, "ระบุชื่อ Showtime/Event เช่น Big Mountain 2026");
}

async function handleShowtimeNumber(text: string, replyToken: string, env: Env): Promise<void> {
  const m = text.match(/^showtime\s+(\d+)$/i);
  const idx = Number(m?.[1] || 0) - 1;
  const events = await listShowtimeEvents(env);
  if (!events[idx]) return reply(env, replyToken, `เลขไม่ถูกต้อง${events.length ? ` (1-${events.length})` : ""}`);
  return replyAuto(env, replyToken, await buildShowtimeCard(env, events[idx].event_name));
}

async function selectShowtimeEvent(text: string, userId: string, groupId: string | null, targetId: string, replyToken: string, env: Env): Promise<void> {
  const m = text.match(/^(?:showtime|edit\s+showtime)\s+(\d+)$/i);
  const idx = Number(m?.[1] || 0) - 1;
  const events = await listShowtimeEvents(env);
  if (!events[idx]) return reply(env, replyToken, `เลขไม่ถูกต้อง${events.length ? ` (1-${events.length})` : ""}`);
  const selected = events[idx];
  const loaded = await loadShowtime(env, selected.event_name);
  await setState(env, userId, groupId, "showtime_mode", {
    event_name: selected.event_name,
    show_date: loaded.show_date || selected.show_date || "",
    target_id: targetId,
    showtime_temp: loaded.schedule,
  });
  return reply(env, replyToken, `เลือก event: ${selected.event_name}\n${await formatShowtimeMessage(env, selected.event_name)}\n\nพิมพ์เวลาใหม่เพื่อแก้/เพิ่ม แล้วพิมพ์ save`);
}

async function handleEndShowtimeCommand(text: string, userId: string, groupId: string | null, replyToken: string, env: Env): Promise<void> {
  const events = await listShowtimeEvents(env);
  if (!events.length) return reply(env, replyToken, "ไม่มี event ในระบบให้ลบ");
  const m = text.match(/^end\s+showtime\s+(\d+)$/i);
  if (!m) return reply(env, replyToken, "เลือก event ที่ต้องการลบ:\n" + events.map((e, i) => `${i + 1}. ${e.event_name} (${e.count} วง)`).join("\n") + "\nพิมพ์ end showtime [เลข]");
  const idx = Number(m[1]) - 1;
  if (!events[idx]) return reply(env, replyToken, `เลขไม่ถูกต้อง (1-${events.length})`);
  await setState(env, userId, groupId, "wait_end_showtime_confirm", { event_name: events[idx].event_name });
  return reply(env, replyToken, `ยืนยันลบ event '${events[idx].event_name}' ทั้งหมด?\nพิมพ์ yes เพื่อยืนยัน หรือ exit เพื่อยกเลิก`);
}

async function handleShowtimeText(text: string, userId: string, groupId: string | null, targetId: string, replyToken: string, state: BotState, env: Env): Promise<void> {
  const lower = text.toLowerCase();
  if (lower === "menu showtime" || lower === "help showtime") return replyFlex(env, replyToken, buildShowtimeMenuFlex());
  if (["exit", "cancel", "ออก", "ยกเลิก"].includes(lower)) {
    await clearState(env, userId);
    return reply(env, replyToken, "ออกจากโหมด Showtime แล้ว");
  }
  if (state.action === "wait_end_showtime_confirm") {
    const eventName = String(state.payload.event_name || "");
    if (["yes", "y"].includes(lower)) {
      await deleteShowtimeEvent(env, eventName);
      await setState(env, userId, groupId, "showtime_mode", { target_id: targetId, showtime_temp: [] });
      return reply(env, replyToken, `ลบ event '${eventName}' แล้ว\nยังอยู่ในโหมด Showtime พิมพ์ exit เพื่อออก`);
    }
    return reply(env, replyToken, "พิมพ์ yes เพื่อยืนยัน หรือ exit เพื่อยกเลิก");
  }
  if (state.action === "wait_showtime_event_name") {
    await setState(env, userId, groupId, "wait_showtime_date", { event_name: text, target_id: targetId });
    return reply(env, replyToken, "ระบุวันที่จัดแสดง YYYY-MM-DD หรือพิมพ์ ข้าม");
  }
  if (state.action === "wait_showtime_date") {
    const showDate = ["ข้าม", "skip"].includes(lower) ? null : text;
    await setState(env, userId, groupId, "showtime_mode", { event_name: state.payload.event_name, show_date: showDate, end_date: showDate, target_id: targetId, showtime_temp: [] });
    return reply(env, replyToken, "เข้าโหมด Showtime แล้ว\nส่งรูปตาราง หรือพิมพ์ตารางหลายบรรทัด เช่น 13:00-13:50 ARTIST\nพิมพ์ save เพื่อบันทึก");
  }
  if (state.action === "showtime_mode") {
    if (lower === "edit showtime") return reply(env, replyToken, "พิมพ์ edit showtime [เลข] เช่น edit showtime 1");
    if (/^edit\s+showtime\s+\d+$/i.test(lower)) return selectShowtimeEvent(text, userId, groupId, targetId, replyToken, env);
    if (/^showtime\s+\d+$/i.test(lower)) return selectShowtimeEvent(text, userId, groupId, targetId, replyToken, env);
    if (lower === "end showtime" || /^end\s+showtime\s+\d+$/i.test(lower)) return handleEndShowtimeCommand(text, userId, groupId, replyToken, env);
    if (lower === "showtime") {
      const events = await listShowtimeEvents(env);
      return reply(env, replyToken, events.length ? buildShowtimeEventList(events) : "ยังไม่มีข้อมูล Showtime");
    }
    if (lower === "เพิ่ม" || lower === "add showtime") {
      await setState(env, userId, groupId, "wait_showtime_event_name", { target_id: targetId });
      return reply(env, replyToken, "ระบุชื่อ Showtime/Event");
    }
    if (lower === "save") {
      const eventName = String(state.payload.event_name || "default");
      const items = ((state.payload.showtime_temp as any[]) || []).map((x) => ({ time: x.time, artist: x.artist }));
      await saveShowtime(env, eventName, String(state.payload.show_date || ""), items);
      await setState(env, userId, groupId, "showtime_mode", { ...state.payload, event_name: eventName, showtime_temp: [], target_id: targetId });
      return reply(env, replyToken, `บันทึก Showtime แล้ว\n${await formatShowtimeMessage(env, eventName)}\n\nยังอยู่ในโหมด Showtime พิมพ์ exit เพื่อออก`);
    }
    const items = extractShowtime(text);
    if (!items.length) return reply(env, replyToken, "ไม่พบรูปแบบเวลา เช่น 13:00-13:50 ARTIST");
    const baseItems = (state.payload.showtime_temp as any[] || []).length
      ? (state.payload.showtime_temp as any[])
      : state.payload.event_name
        ? (await loadShowtime(env, String(state.payload.event_name))).schedule
        : [];
    const merged = mergeShowtimeItems(baseItems, items);
    await setState(env, userId, groupId, "showtime_mode", { ...state.payload, showtime_temp: merged, target_id: targetId });
    return reply(env, replyToken, "รับตารางแล้ว\n" + formatShowtimeItems(merged) + "\nพิมพ์ save เพื่อบันทึก");
  }
}

async function handleEditExpense(text: string, userId: string, groupId: string | null, replyToken: string, env: Env): Promise<void> {
  const m = text.match(/^edit\s+(\d+)\s+(\d+(?:\.\d{1,2})?)/i);
  if (!m) {
    const trip = await getActiveTrip(env, userId, groupId);
    if (!trip) return reply(env, replyToken, "ไม่มีทริปที่กำลังทำงานอยู่");
    const expenses = await getAllExpenses(env, trip.id);
    if (!expenses.length) return reply(env, replyToken, "ยังไม่มีรายการค่าใช้จ่ายให้แก้ไข");
    const recent = expenses.slice(-10).reverse();
    const rates = await getRatesForCurrencies(env, recent.map((e) => e.currency || "THB"));
    const body: FlexNode[] = [flexLabel("รายการล่าสุด")];
    for (const exp of recent) {
      const id = String(exp.id).padStart(4, "0");
      const orig = Number(exp.amount || 0);
      const currency = normalizeCurrencyCode(exp.currency) || "THB";
      const thb = Math.round(orig * (rates.get(currency) ?? 1)).toLocaleString();
      const tag = exp.tag || "#ทั่วไป";
      const payer = String(exp.payer_name || "-");
      const people = participants(exp, exp.payer_name).join(" ");
      body.push(flexKV(`${id} ${tag}`, `${orig.toLocaleString()} ${currency} = ฿${thb}`));
      body.push({ type: "text", text: `จ่าย: ${payer} · หาร: ${people || "-"}`, size: "xxs", color: "#aaaaaa", wrap: true });
    }
    body.push(flexSep(), { type: "text", text: `แก้ยอด: edit [ID] [ยอดใหม่]\nเช่น edit ${String(expenses[expenses.length - 1].id).padStart(4, "0")} 88`, size: "xs", color: "#888888", wrap: true });
    return replyAuto(env, replyToken, flexCard({ altText: `รายการล่าสุดที่แก้ได้ (${recent.length} รายการ)`, title: "แก้ไขรายการ", body }));
  }
  const id = Number(m[1]);
  const amount = Number(m[2]);
  const rows = await supabaseSelect<Expense>(env, "expenses", "*", [`id=eq.${id}`], "limit=1");
  if (!rows.length) return reply(env, replyToken, "⚠️ ไม่พบรายการนี้");
  const currency = rows[0].currency || "THB";
  const amountThb = await computeAmountThb(env, amount, currency);
  await supabasePatch(env, "expenses", { amount, amount_thb: amountThb.amount, exchange_rate_used: amountThb.rate, exchange_rate_source: amountThb.source }, [`id=eq.${id}`]);
  return reply(env, replyToken, `แก้ไข ID ${String(id).padStart(4, "0")} เป็น ${amount.toLocaleString()} ${currency} แล้ว`, QR_MAIN);
}

async function handleEndTrip(userId: string, groupId: string | null, replyToken: string, env: Env): Promise<void> {
  const trip = await getActiveTrip(env, userId, groupId);
  if (!trip) return reply(env, replyToken, "ไม่มีทริปที่กำลังทำงานอยู่");
  await setState(env, userId, groupId, "wait_end_trip_confirm", { trip_id: trip.id });
  return replyAuto(env, replyToken, await buildEndTripSummary(env, trip, { confirm: true }), QR_END_CONFIRM);
}

async function handleEndTripConfirm(text: string, userId: string, groupId: string | null, replyToken: string, state: BotState, env: Env): Promise<void> {
  const lower = text.trim().toLowerCase();
  if (["ยืนยัน", "yes", "ใช่", "ok", "confirm"].includes(lower)) {
    const tripId = String(state.payload.trip_id || "");
    const trips = await supabaseSelect<Trip>(env, "trips", "*", [`id=eq.${tripId}`], "limit=1");
    const trip = trips[0];
    await clearState(env, userId);
    if (!trip) return reply(env, replyToken, "⚠️ ไม่พบทริปนี้แล้ว");
    const summary = await buildEndTripSummary(env, trip);
    await supabasePatch(env, "trips", { status: "closed", currency_code: getTripBaseCurrency(trip) }, [`id=eq.${tripId}`]);
    return replyAuto(env, replyToken, summary, QR_NOTRIP);
  }
  if (["ยกเลิก", "cancel", "exit", "ออก", "ไม่"].includes(lower)) {
    await clearState(env, userId);
    return reply(env, replyToken, "ยกเลิกการจบทริปแล้ว ทริปยังเปิดอยู่", QR_MAIN);
  }
  return reply(env, replyToken, "พิมพ์ ยืนยัน เพื่อจบทริป หรือ ยกเลิก", QR_END_CONFIRM);
}

async function handleHistory(userId: string, groupId: string | null, targetId: string, replyToken: string, env: Env): Promise<void> {
  const trips = await supabaseSelect<Trip>(env, "trips", "*", [], "order=created_at.desc&limit=10");
  if (!trips.length) return reply(env, replyToken, "ยังไม่มีประวัติทริป");
  await setState(env, userId, groupId, "export_history", { trips, target_id: targetId });
  const body: FlexNode[] = [flexLabel("เลือกทริปเพื่อ export")];
  trips.forEach((t, i) => body.push(flexKV(`${i + 1}. ${t.title || "-"}`, t.status || "-")));
  const buttons = trips.slice(0, 4).map((t, i) => ({ label: `Excel: ${String(t.title || ("ทริป " + (i + 1))).slice(0, 18)}`, text: `excel ${i + 1}` }));
  return replyAuto(env, replyToken, flexCard({ altText: `ประวัติทริปล่าสุด (${trips.length} ทริป) — พิมพ์ excel [เลข]`, title: "ประวัติทริป", body, buttons }), qr([["ออก", "exit"]]));
}

async function handleExportHistoryChoice(text: string, userId: string, targetId: string, replyToken: string, state: BotState, env: Env, ctx: ExecutionContext): Promise<void> {
  const m = text.match(/^excel\s+(\d+)$/i) || text.match(/^(\d+)$/);
  if (!m) return reply(env, replyToken, "พิมพ์ excel [เลข] เช่น excel 1\nหรือพิมพ์ exit เพื่อออกจาก history");
  const idx = Number(m[1]) - 1;
  const trips = (state.payload.trips as Trip[]) || [];
  if (!trips[idx]) return reply(env, replyToken, "เลขไม่ถูกต้อง");
  await clearState(env, userId);
  const job = await createExportJob(env, trips[idx], String(state.payload.target_id || targetId), userId);
  dispatchExportJobInBackground(ctx, env, job);
  return reply(env, replyToken, `รับงาน export แล้ว: ${trips[idx].title}\nเสร็จแล้วจะส่งลิงก์กลับใน LINE`);
}

async function handleExportCommand(userId: string, groupId: string | null, targetId: string, replyToken: string, env: Env, ctx: ExecutionContext): Promise<void> {
  const trip = await getActiveTrip(env, userId, groupId);
  if (!trip) return handleHistory(userId, groupId, targetId, replyToken, env);
  const job = await createExportJob(env, trip, targetId, userId);
  dispatchExportJobInBackground(ctx, env, job);
  return reply(env, replyToken, `รับงาน export แล้ว: ${trip.title}\nเสร็จแล้วจะส่งลิงก์กลับใน LINE`);
}

async function handleLinePush(request: Request, env: Env): Promise<Response> {
  const data = await request.json<any>();
  if (!data.targetId || !data.message) return json({ ok: false, error: "ต้องระบุ targetId และ message" }, 400);
  await push(env, data.targetId, String(data.message));
  return json({ ok: true });
}

async function handleSchedules(request: Request, env: Env): Promise<Response> {
  if (request.method === "GET") {
    const rows = await supabaseSelect(env, "schedules", "*", [], "order=created_at.desc");
    return json({ ok: true, schedules: rows.map(formatSchedule) });
  }
  if (request.method === "POST") {
    const data = await request.json<any>();
    const row = await supabaseInsert(env, "schedules", { target_id: data.targetId || "", buyer_name: data.buyerName || "", name: data.name || "", url: data.url || "", sale_time: data.saleTime || "", site: data.site || "", active: true });
    return json({ ok: true, schedule: formatSchedule(row) });
  }
  return json({ ok: false, error: "Method not allowed" }, 405);
}

async function deleteSchedule(request: Request, env: Env): Promise<Response> {
  const id = decodeURIComponent(new URL(request.url).pathname.split("/").pop() || "");
  await supabaseDelete(env, "schedules", [`id=eq.${encodeURIComponent(id)}`]);
  return json({ ok: true });
}

async function handleExportTrip(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
  const data = await request.json<any>();
  const trip = await supabaseSelect<Trip>(env, "trips", "*", [`id=eq.${encodeURIComponent(data.tripId || "")}`], "limit=1");
  if (!trip.length) return json({ ok: false, error: "Trip not found" }, 404);
  const job = await createExportJob(env, trip[0], data.targetId, data.requestedBy || data.targetId);
  dispatchExportJobInBackground(ctx, env, job);
  return json({ ok: true, job });
}

async function authCron(request: Request, env: Env, fn: () => Promise<unknown>): Promise<Response> {
  const secret = env.CRON_SECRET || "";
  if (secret && request.headers.get("Authorization") !== `Bearer ${secret}`) return json({ ok: false, error: "Unauthorized" }, 401);
  return json(await fn());
}

async function runShowtimeCheck(env: Env): Promise<Record<string, unknown>> {
  const now = new Date(Date.now() + 7 * 60 * 60 * 1000);
  const current = now.toISOString().slice(11, 16);
  let alerted = 0;
  const states = await supabaseSelect<BotState>(env, "bot_states", "*", [`action=eq.showtime_mode`]);
  for (const state of states) {
    const eventName = String(state.payload?.event_name || "");
    const target = String(state.payload?.target_id || state.group_id || state.user_id);
    if (!eventName || !target) continue;
    const schedule = await loadShowtime(env, eventName);
    for (const item of schedule.schedule) {
      const start = startHhmm(item.time);
      if (start !== current) continue;
      const key = `${now.toISOString().slice(0, 10)}|${start}|${item.artist || ""}`;
      if (state.payload?.last_alert_key === key) continue;
      await push(env, target, `Showtime Now: ${item.artist || "-"}\nเวลา ${item.time || start}\n${eventName}`);
      await setState(env, state.user_id, state.group_id || null, "showtime_mode", { ...state.payload, last_alert_key: key });
      alerted++;
      break;
    }
  }
  return { ok: true, alerted, serverTime: now.toISOString() };
}

async function runDailySummary(env: Env): Promise<Record<string, unknown>> {
  // cron ยิงตอน 09:00 ไทย แล้วสรุปยอดของ "เมื่อวาน" (วันที่ผ่านมาเต็มวัน)
  const targetDate = thaiDateString(new Date(Date.now() - 24 * 3600 * 1000));
  const trips = await supabaseSelect<Trip>(env, "trips", "*", [`status=eq.active`]);
  let sent = 0;
  for (const trip of trips) {
    const expenses = (await getAllExpenses(env, trip.id)).filter((e) => thaiDateFromIso(e.created_at || "") === targetDate);
    if (!expenses.length) continue;
    let totalThb = 0;
    const categories: Record<string, { total: number; people: Set<string> }> = {};
    for (const exp of expenses) {
      const amount = getExpenseAmountThb(exp);
      totalThb += amount;
      const tag = exp.tag || "#ทั่วไป";
      categories[tag] ||= { total: 0, people: new Set() };
      categories[tag].total += amount;
      for (const p of participants(exp, exp.payer_name)) categories[tag].people.add(p);
    }
    const dailyBody: FlexNode[] = [flexLabel("ยอดรวมทั้งวัน"), bigTotalNode(totalThb), flexSep(), flexLabel("ตามหมวด")];
    for (const [tag, data] of Object.entries(categories)) {
      dailyBody.push(flexKV(tag, baht(data.total)));
      const ppl = Array.from(data.people).join(" ");
      if (ppl) dailyBody.push({ type: "text", text: ppl, size: "xxs", color: "#aaaaaa", wrap: true });
    }
    const target = trip.line_group_id || trip.creator_id;
    if (target) {
      await pushFlex(env, target, flexCard({ altText: `สรุปยอด ${targetDate}: ${Math.round(totalThb).toLocaleString()} บาท`, title: "สรุปยอดประจำวัน", subtitle: `${trip.title} · ${targetDate}`, body: dailyBody }));
      sent++;
    }
    await supabaseUpsert(env, "daily_summaries", { trip_id: trip.id, summary_date: targetDate, total_thb: totalThb, details: Object.fromEntries(Object.entries(categories).map(([k, v]) => [k, { total_thb: v.total, participants: Array.from(v.people) }])) }, "trip_id,summary_date");
  }
  return { ok: true, sent, date: targetDate };
}

async function verifyLineSignature(body: string, signature: string, secret: string): Promise<boolean> {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const digest = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(body));
  return btoa(String.fromCharCode(...new Uint8Array(digest))) === signature;
}

async function reply(env: Env, replyToken: string, text: string, quick?: Record<string, unknown>): Promise<void> {
  const msg: Record<string, unknown> = { type: "text", text: truncateLineText(text) };
  if (quick) msg.quickReply = quick;
  await replyMessages(env, replyToken, [msg]);
}

async function replyFlex(env: Env, replyToken: string, flex: Record<string, unknown>, quick?: Record<string, unknown>): Promise<void> {
  const message = quick ? { ...flex, quickReply: quick } : flex;
  try {
    await replyMessages(env, replyToken, [message]);
  } catch (error) {
    console.error("replyFlex failed, falling back to text", errorMessage(error));
    await reply(env, replyToken, String(flex.altText || "ส่งการ์ดไม่ได้ ลองใหม่อีกครั้ง"), quick);
  }
}

async function replyMessages(env: Env, replyToken: string, messages: unknown[]): Promise<void> {
  await lineFetch(env, "/v2/bot/message/reply", { replyToken, messages });
}

async function push(env: Env, to: string, text: string): Promise<void> {
  await lineFetch(env, "/v2/bot/message/push", { to, messages: [{ type: "text", text: truncateLineText(text) }] });
}

async function pushFlex(env: Env, to: string, flex: Record<string, unknown>): Promise<void> {
  try {
    await lineFetch(env, "/v2/bot/message/push", { to, messages: [flex] });
  } catch (error) {
    console.error("pushFlex failed, falling back to text", errorMessage(error));
    await push(env, to, String(flex.altText || "ส่งการ์ดไม่ได้"));
  }
}

// ส่งแบบยืดหยุ่น: ถ้าเป็น object = Flex card, ถ้าเป็น string = ข้อความ
async function replyAuto(env: Env, replyToken: string, msg: string | Record<string, unknown>, quick?: Record<string, unknown>): Promise<void> {
  if (typeof msg === "string") await reply(env, replyToken, msg, quick);
  else await replyFlex(env, replyToken, msg, quick);
}

// ===== Quick Reply เมนูต่อโหมด =====
function qr(pairs: Array<[string, string]>): Record<string, unknown> {
  return { items: pairs.map(([label, text]) => ({ type: "action", action: { type: "message", label, text } })) };
}
const QR_MAIN = qr([["ยอดวันนี้", "ยอดวันนี้"], ["ยอดรวม", "ยอด"], ["ประวัติ", "history"], ["จบทริป", "จบทริป"]]);
const QR_NOTRIP = qr([["➕ สร้างทริป", "ทริป"], ["ประวัติ", "history"], ["ช่วยเหลือ", "help"]]);
const QR_CANCEL = qr([["ยกเลิก", "ยกเลิก"]]);
const QR_COUNTRY = qr([["ไทย", "ไทย"], ["ญี่ปุ่น", "ญี่ปุ่น"], ["เกาหลี", "เกาหลี"], ["จีน", "จีน"], ["ยกเลิก", "ยกเลิก"]]);
const QR_SLIP_CONFIRM = qr([["ใช่", "ใช่"], ["ยกเลิก", "ยกเลิก"]]);
const QR_END_CONFIRM = qr([["ยืนยัน", "ยืนยัน"], ["ยกเลิก", "ยกเลิก"]]);

// ===== Flex helpers (การ์ดหัวสีม่วงให้เข้ากับเมนู showtime เดิม) =====
const FLEX_ACCENT = "#7C3AED";

type FlexNode = Record<string, unknown>;

// รูปแบบเงินในการ์ด ใช้ ฿ นำหน้า + คอมมา ให้สม่ำเสมอ
function baht(n: number): string {
  return `฿${Math.round(n).toLocaleString()}`;
}
function baht2(n: number): string {
  return `฿${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function flexKV(label: string, value: string, opts: { valueColor?: string; bold?: boolean } = {}): FlexNode {
  return {
    type: "box", layout: "horizontal", contents: [
      { type: "text", text: label, size: "sm", color: "#666666", flex: 5, wrap: true },
      { type: "text", text: value, size: "sm", color: opts.valueColor || "#222222", weight: opts.bold ? "bold" : "regular", align: "end", flex: 4, wrap: true },
    ],
  };
}

function flexLabel(text: string): FlexNode {
  return { type: "text", text, size: "xs", color: "#999999", margin: "md" };
}

function flexSep(): FlexNode {
  return { type: "separator", margin: "md" };
}

function flexCard(opts: { altText: string; title: string; subtitle?: string; body: FlexNode[]; buttons?: Array<{ label: string; text: string }> }): FlexNode {
  const header: FlexNode = {
    type: "box", layout: "vertical", backgroundColor: FLEX_ACCENT, paddingAll: "14px",
    contents: [
      { type: "text", text: opts.title, color: "#FFFFFF", weight: "bold", size: "lg", wrap: true },
      ...(opts.subtitle ? [{ type: "text", text: opts.subtitle, color: "#E9E2FB", size: "xs", margin: "sm", wrap: true }] : []),
    ],
  };
  const bubble: FlexNode = {
    type: "bubble", size: "mega",
    header,
    body: { type: "box", layout: "vertical", spacing: "sm", contents: opts.body },
  };
  if (opts.buttons?.length) {
    bubble.footer = {
      type: "box", layout: "vertical", spacing: "sm", flex: 0,
      contents: opts.buttons.map((b, i) => ({
        type: "button", style: i === 0 ? "primary" : "secondary", color: i === 0 ? FLEX_ACCENT : undefined, height: "sm",
        action: { type: "message", label: b.label, text: b.text },
      })),
    };
  }
  return { type: "flex", altText: opts.altText.slice(0, 400), contents: bubble };
}

function buildSaveCard(opts: { amount: number; currency: string; tag?: string | null; people: string[]; payer?: string; id?: number | string | null; prefix?: string }): FlexNode {
  const body: FlexNode[] = [];
  if (opts.prefix) body.push({ type: "text", text: opts.prefix, size: "xs", color: "#888888", wrap: true });
  body.push(flexLabel("บันทึกแล้ว"), { type: "text", text: `${opts.amount.toLocaleString()} ${opts.currency}`, size: "xxl", weight: "bold", color: "#222222" });
  if (opts.payer) body.push(flexKV("ผู้จ่าย", opts.payer));
  body.push(flexKV("หาร", opts.people.join(" ") || "-"));
  if (opts.id != null) body.push({ type: "text", text: `แก้ยอด: edit ${String(opts.id).padStart(4, "0")} [ยอดใหม่]`, size: "xxs", color: "#aaaaaa", margin: "md", wrap: true });
  return flexCard({
    altText: `บันทึก ${opts.amount.toLocaleString()} ${opts.currency}${opts.tag ? ` (${opts.tag})` : ""}`,
    title: "บันทึกรายการ", subtitle: opts.tag || undefined, body,
    buttons: [{ label: "ยอดวันนี้", text: "ยอดวันนี้" }, { label: "ยอดรวมทริป", text: "ยอด" }],
  });
}

async function lineFetch(env: Env, path: string, body: unknown): Promise<any> {
  const res = await fetch(`https://api.line.me${path}`, { method: "POST", headers: { Authorization: `Bearer ${env.LINE_CHANNEL_ACCESS_TOKEN}`, "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) throw new Error(`LINE API ${res.status}: ${await res.text()}`);
  return res.text();
}

async function getLineContent(env: Env, messageId: string): Promise<ArrayBuffer> {
  const res = await fetchWithTimeout(`https://api-data.line.me/v2/bot/message/${messageId}/content`, { headers: { Authorization: `Bearer ${env.LINE_CHANNEL_ACCESS_TOKEN}` } }, 8000);
  if (!res.ok) throw new Error(`LINE content ${res.status}`);
  return res.arrayBuffer();
}

async function ocrLineImage(env: Env, messageId: string): Promise<string | null> {
  try {
    if (!env.OCR_SPACE_API_KEY) return null;
    const image = await getLineContent(env, messageId);
    const form = new FormData();
    form.append("apikey", env.OCR_SPACE_API_KEY);
    form.append("language", "tha");
    form.append("OCREngine", "2");
    form.append("file", new Blob([image], { type: "image/jpeg" }), "line.jpg");
    const res = await fetchWithTimeout("https://api.ocr.space/parse/image", { method: "POST", body: form }, 12000);
    if (!res.ok) {
      console.error("OCR.space failed", res.status, await res.text());
      return null;
    }
    const data = await res.json<any>();
    if (data.IsErroredOnProcessing) {
      console.error("OCR.space processing error", JSON.stringify(data));
      return null;
    }
    return (data.ParsedResults || []).map((r: any) => r.ParsedText || "").join("\n").trim();
  } catch (error) {
    console.error("OCR failed", errorMessage(error));
    return null;
  }
}

async function detectSlipAmounts(env: Env, messageIds: string[]): Promise<Array<number | null>> {
  const amounts: Array<number | null> = [];
  for (const messageId of messageIds) {
    const text = await ocrLineImage(env, messageId);
    const amount = extractAmount(text || "");
    amounts.push(amount || null);
  }
  return amounts;
}

async function fetchWithTimeout(input: RequestInfo, init: RequestInit, timeoutMs: number): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort("timeout"), timeoutMs);
  try {
    return await fetch(input, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

async function getState(env: Env, userId: string): Promise<BotState | null> {
  const rows = await supabaseSelect<BotState>(env, "bot_states", "*", [`user_id=eq.${encodeURIComponent(userId)}`], "limit=1");
  const state = rows[0];
  if (!state) return null;
  if (state.expires_at && new Date(state.expires_at).getTime() < Date.now()) {
    await clearState(env, userId);
    return null;
  }
  return state;
}

async function setState(env: Env, userId: string, groupId: string | null, action: string, payload: Record<string, unknown>): Promise<void> {
  await supabaseUpsert(env, "bot_states", { user_id: userId, group_id: groupId, action, payload, expires_at: new Date(Date.now() + STATE_TIMEOUT_SECONDS * 1000).toISOString(), updated_at: new Date().toISOString() }, "user_id");
}

async function clearState(env: Env, userId: string): Promise<void> {
  await supabaseDelete(env, "bot_states", [`user_id=eq.${encodeURIComponent(userId)}`]);
}

async function deleteExpiredStates(env: Env): Promise<void> {
  await supabaseDelete(env, "bot_states", [`expires_at=lt.${new Date().toISOString()}`]);
}

async function supabaseSelect<T>(env: Env, table: string, select = "*", filters: string[] = [], extra = ""): Promise<T[]> {
  const qs = [`select=${encodeURIComponent(select)}`, ...filters, extra].filter(Boolean).join("&");
  const res = await fetch(`${env.SUPABASE_URL}/rest/v1/${table}?${qs}`, { headers: supabaseHeaders(env) });
  if (!res.ok) throw new Error(`Supabase select ${table}: ${res.status} ${await res.text()}`);
  return res.json();
}

async function supabaseInsert<T>(env: Env, table: string, row: Record<string, unknown>): Promise<T> {
  const res = await fetch(`${env.SUPABASE_URL}/rest/v1/${table}`, { method: "POST", headers: { ...supabaseHeaders(env), Prefer: "return=representation" }, body: JSON.stringify(row) });
  if (!res.ok) throw new Error(`Supabase insert ${table}: ${res.status} ${await res.text()}`);
  const rows = await res.json<T[]>();
  return rows[0];
}

async function supabaseUpsert<T>(env: Env, table: string, row: Record<string, unknown>, onConflict: string): Promise<T[]> {
  const res = await fetch(`${env.SUPABASE_URL}/rest/v1/${table}?on_conflict=${encodeURIComponent(onConflict)}`, { method: "POST", headers: { ...supabaseHeaders(env), Prefer: "resolution=merge-duplicates,return=representation" }, body: JSON.stringify(row) });
  if (!res.ok) throw new Error(`Supabase upsert ${table}: ${res.status} ${await res.text()}`);
  return res.json();
}

async function supabasePatch(env: Env, table: string, row: Record<string, unknown>, filters: string[]): Promise<void> {
  const res = await fetch(`${env.SUPABASE_URL}/rest/v1/${table}?${filters.join("&")}`, { method: "PATCH", headers: { ...supabaseHeaders(env), Prefer: "return=minimal" }, body: JSON.stringify(row) });
  if (!res.ok) throw new Error(`Supabase patch ${table}: ${res.status} ${await res.text()}`);
}

async function supabaseDelete(env: Env, table: string, filters: string[]): Promise<void> {
  const res = await fetch(`${env.SUPABASE_URL}/rest/v1/${table}?${filters.join("&")}`, { method: "DELETE", headers: { ...supabaseHeaders(env), Prefer: "return=minimal" } });
  if (!res.ok) throw new Error(`Supabase delete ${table}: ${res.status} ${await res.text()}`);
}

function supabaseHeaders(env: Env): HeadersInit {
  const key = env.SUPABASE_SERVICE_ROLE_KEY || env.SUPABASE_KEY || env.SUPABASE_ANON_KEY;
  if (!key) throw new Error("Missing SUPABASE_SERVICE_ROLE_KEY, SUPABASE_KEY, or SUPABASE_ANON_KEY");
  return { apikey: key, Authorization: `Bearer ${key}`, "Content-Type": "application/json" };
}

async function getActiveTrip(env: Env, userId: string, groupId: string | null): Promise<Trip | null> {
  if (groupId) {
    const groupRows = await supabaseSelect<Trip>(env, "trips", "*", [`status=eq.active`, `line_group_id=eq.${encodeURIComponent(groupId)}`], "order=created_at.desc&limit=1");
    if (groupRows[0]) return groupRows[0];
  }
  const rows = await supabaseSelect<Trip>(env, "trips", "*", [`status=eq.active`, `creator_id=eq.${encodeURIComponent(userId)}`], "order=created_at.desc&limit=1");
  return rows[0] || null;
}

async function getAllExpenses(env: Env, tripId: string): Promise<Expense[]> {
  return supabaseSelect<Expense>(env, "expenses", "*", [`trip_id=eq.${encodeURIComponent(tripId)}`], "order=id.asc");
}

async function saveExpense(env: Env, tripId: string, userId: string, payer: string, item: string, amount: number, currency: string, tag: string | null, people: string[], slipUrl: string | null = null): Promise<any> {
  const amountThb = await computeAmountThb(env, amount, currency);
  return supabaseInsert(env, "expenses", { trip_id: tripId, line_user_id: payer, created_by_user_id: userId, payer_name: payer, amount, amount_thb: amountThb.amount, exchange_rate_used: amountThb.rate, exchange_rate_source: amountThb.source, item_name: item || "ค่าใช้จ่าย", currency, tag, participants: people, slip_url: slipUrl });
}

async function getDisplayName(env: Env, userId: string, groupId: string | null): Promise<string> {
  const path = groupId ? `/v2/bot/group/${groupId}/member/${userId}` : `/v2/bot/profile/${userId}`;
  try {
    const res = await fetch(`https://api.line.me${path}`, { headers: { Authorization: `Bearer ${env.LINE_CHANNEL_ACCESS_TOKEN}` } });
    if (!res.ok) return userId.slice(0, 8);
    return (await res.json<any>()).displayName || userId.slice(0, 8);
  } catch {
    return userId.slice(0, 8);
  }
}

// รูปแบบ canonical: <ผู้จ่าย> #หมวด <ยอด> [สกุล] <คนหาร...>
// (#หมวด แทน "รายการ"; ชื่อแรกก่อนยอด = ผู้จ่าย, ชื่อหลังยอด = คนหาร)
export function parseExpense(text: string, defaultPayer: string, defaultCurrency = "THB") {
  const parts = text.split(/\s+/).filter(Boolean);
  const amtIdx = parts.findIndex((p) => /^\d+(?:\.\d{1,2})?$/.test(p.replace(/,/g, "")));
  if (amtIdx < 0) return null;
  const amount = Number(parts[amtIdx].replace(/,/g, ""));

  // สกุลเงิน: รับ strict 4 ตัวถ้าอยู่ติดหลังยอด (กันชื่อเล่นชนรหัสสกุล), มิฉะนั้นใช้สกุลทริป
  let afterStart = amtIdx + 1;
  const inlineCurrency = normalizeCurrency(parts[afterStart] || "");
  const fallbackCurrency = ISO_4217.has(normalizeCurrencyCode(defaultCurrency))
    ? normalizeCurrencyCode(defaultCurrency)
    : "THB";
  const currency = inlineCurrency || fallbackCurrency;
  if (inlineCurrency) afterStart++;

  // เก็บ tag (token แรกที่ขึ้นต้น #) + ชื่อก่อน/หลังยอด
  let tag: string | null = null;
  const before: string[] = [];
  for (let k = 0; k < amtIdx; k++) {
    const t = parts[k];
    if (t.startsWith("#")) { if (!tag) tag = t; continue; }
    before.push(t);
  }
  const after: string[] = [];
  for (let k = afterStart; k < parts.length; k++) {
    const t = parts[k];
    if (t.startsWith("#")) { if (!tag) tag = t; continue; }
    after.push(t);
  }
  const payer = before.length ? before[0] : defaultPayer;
  const participants = after;
  if (!participants.length) return null;
  const item = tag ? tag.replace(/^#/, "") : "ค่าใช้จ่าย";
  return { payer, item, amount, currency, tag, participants };
}

// รูปแบบ slip: <ผู้จ่าย> #หมวด <คนหาร...> (ชื่อแรก = ผู้จ่าย, ที่เหลือ = คนหาร)
export function parseSlipAssignment(text: string) {
  const tokens = text.split(/\s+/).filter(Boolean);
  let tag: string | null = null;
  let currency: string | null = null;
  const names: string[] = [];
  for (const token of tokens) {
    if (token.startsWith("#") && !tag) tag = token;
    else if (normalizeCurrency(token) && !currency) currency = normalizeCurrency(token);
    else names.push(token);
  }
  const payer = names[0] || "";
  const participants = names.slice(1);
  return { payer, participants, tag, currency };
}

function parsePositiveAmount(text: string): number | null {
  const cleaned = text.trim().replace(/,/g, "");
  if (!/^\d+(?:\.\d{1,2})?$/.test(cleaned)) return null;
  const amount = Number(cleaned);
  return amount > 0 && amount <= 1000000 ? amount : null;
}

function normalizeCurrency(v: string | null | undefined): string | null {
  const x = String(v || "").trim().toUpperCase();
  return ["THB", "JPY", "USD", "KRW"].includes(x) ? x : null;
}

export function resolveBaseCurrency(input: string): string | null {
  const code = normalizeCurrencyCode(input);
  if (ISO_4217.has(code)) return code;
  const byCountry = COUNTRY_TO_CURRENCY[normalizeCountryName(input)];
  return byCountry || null;
}

function getTripBaseCurrency(trip: Trip | null): string {
  const raw = normalizeCurrencyCode(trip?.base_currency || trip?.currency_code);
  return ISO_4217.has(raw) ? raw : "THB";
}

async function computeAmountThb(env: Env, amount: number, currency: string): Promise<{ amount: number; rate: number; source: string }> {
  const curr = normalizeCurrencyCode(currency) || "THB";
  if (curr === "THB") return { amount, rate: 1, source: "same_currency" };
  const rate = await getRateThb(env, curr); // เรทสดแบบ cache (12 ชม.) มี fallback ในตัว
  return { amount: amount * rate, rate, source: "live" };
}

const FX_CACHE_TTL_MS = 12 * 60 * 60 * 1000; // 12h

async function fetchLiveRateThb(currency: string): Promise<number | null> {
  try {
    const res = await fetchWithTimeout(
      `https://open.er-api.com/v6/latest/${currency}`,
      { method: "GET" },
      5000,
    );
    if (!res.ok) return null;
    const data = (await res.json()) as { result?: string; rates?: Record<string, number> };
    if (data?.result !== "success") return null;
    const rate = data.rates?.THB;
    return typeof rate === "number" && rate > 0 ? rate : null;
  } catch {
    return null;
  }
}

export async function getRateThb(env: Env, currency: string): Promise<number> {
  const curr = normalizeCurrencyCode(currency);
  if (curr === "THB" || !curr) return 1;

  // อ่าน cache แบบ degrade ได้: ถ้า Supabase ล่มก็ไปต่อ (ดึงสด/fallback) ไม่ throw
  let cached: any = null;
  try {
    const rows = await supabaseSelect<any>(env, "fx_rates", "*", [`currency=eq.${curr}`], "limit=1");
    cached = rows?.[0] ?? null;
  } catch {
    cached = null;
  }
  if (cached && Date.now() - Date.parse(cached.updated_at) < FX_CACHE_TTL_MS) {
    return Number(cached.rate_thb);
  }

  const live = await fetchLiveRateThb(curr);
  if (live) {
    try {
      await supabaseUpsert(env, "fx_rates",
        { currency: curr, rate_thb: live, updated_at: new Date().toISOString() }, "currency");
    } catch {
      // เขียน cache ไม่ได้ก็ยังใช้เรทสดที่ได้มา
    }
    return live;
  }

  if (cached) return Number(cached.rate_thb);
  return FALLBACK_RATES[curr] ?? 1;
}

async function getRatesForCurrencies(env: Env, currencies: string[]): Promise<Map<string, number>> {
  const distinct = Array.from(new Set(currencies.map((c) => normalizeCurrencyCode(c) || "THB")));
  const map = new Map<string, number>();
  for (const c of distinct) map.set(c, await getRateThb(env, c));
  return map;
}

function getExpenseAmountThb(exp: Expense): number {
  return Number(exp.amount_thb ?? exp.amount ?? 0);
}

function participants(exp: Expense, fallback = ""): string[] {
  const raw = exp.participants;
  const arr = Array.isArray(raw) ? raw : String(raw || "").split(/\s+/);
  const out = arr.map((p) => String(p).trim()).filter(Boolean);
  return out.length ? out : fallback ? [fallback] : [];
}

function formatMoneyLines(values: Record<string, number>): string {
  const entries = Object.entries(values).sort(([a], [b]) => a.localeCompare(b, "th"));
  return entries.length ? entries.map(([name, amount]) => `${name}: ${amount.toLocaleString()} บาท`).join("\n") : "-";
}

type CategoryAgg = Record<string, { byCur: Record<string, number>; thb: number; people: Set<string> }>;

function addCategory(categories: CategoryAgg, exp: Expense, rates: Map<string, number>): void {
  const tag = exp.tag || "#ทั่วไป";
  const cur = normalizeCurrencyCode(exp.currency) || "THB";
  const orig = Number(exp.amount || 0);
  categories[tag] ||= { byCur: {}, thb: 0, people: new Set() };
  categories[tag].byCur[cur] = (categories[tag].byCur[cur] || 0) + orig;
  categories[tag].thb += orig * (rates.get(cur) ?? 1);
  for (const p of participants(exp, exp.payer_name)) categories[tag].people.add(p);
}

function formatCategorySummary(categories: CategoryAgg): string {
  return Object.entries(categories)
    .sort(([a], [b]) => a.localeCompare(b, "th"))
    .map(([tag, data]) => {
      const orig = Object.entries(data.byCur)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([cur, amt]) => `${amt.toLocaleString()} ${cur}`)
        .join(" + ");
      const people = Array.from(data.people).sort((a, b) => a.localeCompare(b, "th")).join(" ");
      return `${tag} ${orig} | ${Math.round(data.thb).toLocaleString()} บาท | ${people}`.trim();
    })
    .join("\n") || "-";
}

// รวมยอดเดิมต่อสกุล + THB สด; คืนบรรทัดต่อสกุลและยอดรวม THB
function summarizeByCurrency(
  expenses: Expense[],
  rates: Map<string, number>,
): { lines: string[]; grandThb: number } {
  const byCur: Record<string, { orig: number; thb: number }> = {};
  let grandThb = 0;
  for (const e of expenses) {
    const cur = normalizeCurrencyCode(e.currency) || "THB";
    const orig = Number(e.amount || 0);
    const thb = orig * (rates.get(cur) ?? 1);
    (byCur[cur] ||= { orig: 0, thb: 0 }).orig += orig;
    byCur[cur].thb += thb;
    grandThb += thb;
  }
  const lines = Object.entries(byCur)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([cur, v]) => `${v.orig.toLocaleString()} ${cur} | ${Math.round(v.thb).toLocaleString()} บาท`);
  return { lines, grandThb };
}

// THB สดของ expense เดียว (ใช้ในหมวด/หารรายคน)
function expenseThbLive(exp: Expense, rates: Map<string, number>): number {
  const cur = normalizeCurrencyCode(exp.currency) || "THB";
  return Number(exp.amount || 0) * (rates.get(cur) ?? 1);
}

// body ของการ์ดสรุป: ยอดตามสกุล + ตามหมวด (THB สด)
function summaryCardBody(expenses: Expense[], rates: Map<string, number>): { body: FlexNode[]; grandThb: number } {
  const byCur: Record<string, { orig: number; thb: number }> = {};
  let grandThb = 0;
  for (const e of expenses) {
    const cur = normalizeCurrencyCode(e.currency) || "THB";
    const orig = Number(e.amount || 0);
    const thb = orig * (rates.get(cur) ?? 1);
    (byCur[cur] ||= { orig: 0, thb: 0 }).orig += orig;
    byCur[cur].thb += thb;
    grandThb += thb;
  }
  const body: FlexNode[] = [flexLabel("ยอดตามสกุล")];
  for (const [cur, v] of Object.entries(byCur).sort(([a], [b]) => a.localeCompare(b))) {
    body.push(flexKV(`${v.orig.toLocaleString()} ${cur}`, baht(v.thb)));
  }
  const categories: CategoryAgg = {};
  for (const e of expenses) addCategory(categories, e, rates);
  const catEntries = Object.entries(categories).sort(([a], [b]) => a.localeCompare(b, "th"));
  if (catEntries.length) {
    body.push(flexSep(), flexLabel("ตามหมวด"));
    for (const [tag, data] of catEntries) {
      const orig = Object.entries(data.byCur).sort(([a], [b]) => a.localeCompare(b)).map(([c, a2]) => `${a2.toLocaleString()} ${c}`).join(" + ");
      body.push(flexKV(`${tag} (${orig})`, baht(data.thb)));
      const ppl = Array.from(data.people).sort((a, b) => a.localeCompare(b, "th")).join(" ");
      if (ppl) body.push({ type: "text", text: ppl, size: "xxs", color: "#aaaaaa", wrap: true });
    }
  }
  return { body, grandThb };
}

function bigTotalNode(grandThb: number): FlexNode {
  return { type: "text", text: `฿${Math.round(grandThb).toLocaleString()}`, size: "xxl", weight: "bold", color: "#222222" };
}

async function buildTripTotalMessage(env: Env, userId: string, groupId: string | null): Promise<string | FlexNode> {
  const trip = await getActiveTrip(env, userId, groupId);
  if (!trip) return "ไม่มีทริปที่กำลังทำงานอยู่";
  const expenses = await getAllExpenses(env, trip.id);
  if (!expenses.length) return "ยังไม่มีรายการค่าใช้จ่าย";
  const rates = await getRatesForCurrencies(env, expenses.map((e) => e.currency || "THB"));
  const { body, grandThb } = summaryCardBody(expenses, rates);
  return flexCard({
    altText: `ยอดรวมทริป ${trip.title}: ${Math.round(grandThb).toLocaleString()} บาท`,
    title: "ยอดรวมทริป",
    subtitle: trip.title,
    body: [flexLabel("ยอดรวมทั้งทริป"), bigTotalNode(grandThb), flexSep(), ...body],
    buttons: [{ label: "ดาวน์โหลด Excel", text: "excel" }, { label: "ยอดวันนี้", text: "ยอดวันนี้" }],
  });
}

async function buildTodayMessage(env: Env, userId: string, groupId: string | null): Promise<string | FlexNode> {
  const trip = await getActiveTrip(env, userId, groupId);
  if (!trip) return "ไม่มีทริปที่กำลังทำงานอยู่";
  const today = thaiDateString(new Date());
  const expenses = (await getAllExpenses(env, trip.id)).filter((e) => thaiDateFromIso(e.created_at || "") === today);
  if (!expenses.length) return `วันนี้ (${today}) ยังไม่มีรายจ่าย`;
  const rates = await getRatesForCurrencies(env, expenses.map((e) => e.currency || "THB"));
  const { body, grandThb } = summaryCardBody(expenses, rates);
  return flexCard({
    altText: `ยอดวันนี้ ${today}: ${Math.round(grandThb).toLocaleString()} บาท`,
    title: "ยอดวันนี้",
    subtitle: `${trip.title} · ${today}`,
    body: [flexLabel("รวมวันนี้"), bigTotalNode(grandThb), flexSep(), ...body],
    buttons: [{ label: "ยอดรวมทริป", text: "ยอด" }],
  });
}

async function buildEndTripSummary(env: Env, trip: Trip, opts: { confirm?: boolean } = {}): Promise<string | FlexNode> {
  const expenses = await getAllExpenses(env, trip.id);
  if (!expenses.length) return opts.confirm ? `ทริป: ${trip.title}\nไม่มีรายการค่าใช้จ่าย — พิมพ์ ยืนยัน เพื่อปิดทริป หรือ ยกเลิก` : `ทริป: ${trip.title}\nไม่มีรายการค่าใช้จ่ายให้หาร`;
  let total = 0;
  const totalByPerson: Record<string, number> = {};
  const paidByPerson: Record<string, number> = {};
  const rates = await getRatesForCurrencies(env, expenses.map((e) => e.currency || "THB"));
  for (const exp of expenses) {
    const amount = expenseThbLive(exp, rates);
    total += amount;
    const people = participants(exp, exp.payer_name);
    const share = amount / Math.max(people.length, 1);
    for (const p of people) totalByPerson[p] = (totalByPerson[p] || 0) + share;
    const payerName = String(exp.payer_name || "").trim();
    if (payerName) paidByPerson[payerName] = (paidByPerson[payerName] || 0) + amount;
  }

  const body: FlexNode[] = [flexLabel("ยอดรวมทั้งทริป"), bigTotalNode(total)];

  body.push(flexSep(), flexLabel("ต้องจ่าย (ต่อคน)"));
  for (const [p, v] of Object.entries(totalByPerson).sort()) body.push(flexKV(p, baht2(v)));

  const paidEntries = Object.entries(paidByPerson).sort();
  if (paidEntries.length) {
    body.push(flexSep(), flexLabel("จ่ายไปแล้ว"));
    for (const [p, v] of paidEntries) body.push(flexKV(p, baht2(v)));
  }

  body.push(flexSep(), flexLabel("สรุปโอนเงิน 💸"));
  const transfers = computeSettlement(paidByPerson, totalByPerson);
  if (transfers.length) {
    for (const t of transfers) body.push(flexKV(`${t.from} → ${t.to}`, baht2(t.amount), { bold: true, valueColor: FLEX_ACCENT }));
  } else {
    body.push({ type: "text", text: "ไม่มียอดต้องโอน (จ่ายตรงกับที่ต้องจ่าย)", size: "sm", color: "#666666", wrap: true });
  }

  const dateSub = trip.start_date ? `${String(trip.start_date).slice(0, 10)}${trip.end_date ? ` ถึง ${String(trip.end_date).slice(0, 10)}` : ""}` : "";
  const subtitle = dateSub ? `${trip.title} · ${dateSub}` : trip.title;
  if (opts.confirm) {
    body.unshift({ type: "text", text: "ปิดทริปแล้วแก้ไม่ได้ ยืนยันหรือไม่?", size: "xs", color: "#C0392B", wrap: true });
    return flexCard({
      altText: `ยืนยันจบทริป ${trip.title}? รวม ${baht(total)}`,
      title: "ยืนยันจบทริป?", subtitle, body,
      buttons: [{ label: "ยืนยันจบทริป", text: "ยืนยัน" }, { label: "ยกเลิก", text: "ยกเลิก" }],
    });
  }
  return flexCard({
    altText: `จบทริป ${trip.title}: รวม ${baht(total)}`,
    title: "จบทริป", subtitle, body,
    buttons: [{ label: "ดาวน์โหลด Excel", text: "excel" }],
  });
}

function fmt2(n: number): string {
  return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// จับคู่ลูกหนี้-เจ้าหนี้ให้จำนวนการโอนน้อยที่สุด (greedy)
export function computeSettlement(
  paid: Record<string, number>,
  owed: Record<string, number>,
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
  let i = 0;
  let j = 0;
  while (i < debtors.length && j < creditors.length) {
    const pay = Math.min(debtors[i].amt, creditors[j].amt);
    transfers.push({ from: debtors[i].name, to: creditors[j].name, amount: pay });
    debtors[i].amt -= pay;
    creditors[j].amt -= pay;
    if (debtors[i].amt < 0.01) i++;
    if (creditors[j].amt < 0.01) j++;
  }
  return transfers;
}

async function findSimilarPeopleNames(env: Env, tripId: string, names: string[]): Promise<Array<[string, string]>> {
  const expenses = await getAllExpenses(env, tripId);
  const known = new Set<string>();
  for (const exp of expenses) for (const p of participants(exp)) known.add(p);
  const warnings: Array<[string, string]> = [];
  for (const name of names) for (const k of known) if (name !== k && similarity(name.toLowerCase(), k.toLowerCase()) >= 0.75) {
    warnings.push([name, k]);
    break;
  }
  return warnings;
}

function similarity(a: string, b: string): number {
  const m = Array(a.length + 1).fill(0).map(() => Array(b.length + 1).fill(0));
  for (let i = 0; i <= a.length; i++) m[i][0] = i;
  for (let j = 0; j <= b.length; j++) m[0][j] = j;
  for (let i = 1; i <= a.length; i++) for (let j = 1; j <= b.length; j++) m[i][j] = Math.min(m[i - 1][j] + 1, m[i][j - 1] + 1, m[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
  return 1 - m[a.length][b.length] / Math.max(a.length, b.length, 1);
}

function buildSimilarNameWarning(warnings: Array<[string, string]>): string {
  return "พบชื่อใกล้เคียง ยังไม่บันทึกรายการนี้\n" + warnings.map(([a, b]) => `${a} ใกล้กับ ${b}`).join("\n") + "\nกรุณาพิมพ์ใหม่โดยใช้ชื่อให้ตรงกัน";
}

function extractAmount(text: string): number | null {
  const money = /(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)/g;
  const baht = [...text.matchAll(new RegExp(money.source + "\\s*บาท", "g"))].map((m) => Number(m[1].replace(/,/g, ""))).filter((n) => n >= 1 && n <= 1000000);
  if (baht.length) return Math.max(...baht);
  const dec = [...text.matchAll(/(?<![\d/:-])(\d{1,3}(?:,\d{3})*(?:\.\d{2}))(?![\d])/g)].map((m) => Number(m[1].replace(/,/g, ""))).filter((n) => n >= 1 && n <= 1000000);
  return dec.length ? Math.max(...dec) : null;
}

function extractShowtime(text: string): Array<{ time: string; artist: string }> {
  const lines = text.split(/\n+/).map((x) => x.trim()).filter(Boolean);
  const out: Array<{ time: string; artist: string }> = [];
  const re = /(\d{1,2}[:.]\d{2})\s*[-–]\s*(\d{1,2}[:.]\d{2})/;
  let previousText = "";
  for (const line of lines) {
    const m = line.match(re);
    if (!m) {
      previousText = line;
      continue;
    }
    const time = `${m[1]}-${m[2]}`.replace(/\./g, ":");
    const inlineArtist = line.replace(re, "").replace(/^[^\wก-๙A-Za-z0-9]+/, "").trim();
    const artist = inlineArtist || previousText || "Unknown";
    out.push({ time, artist });
    previousText = "";
  }
  return sortShowtime(out);
}

function sortShowtime(items: Array<{ time: string; artist: string }>) {
  return items.sort((a, b) => minutes(a.time) - minutes(b.time));
}

function mergeShowtimeItems(base: Array<{ time: string; artist: string }>, updates: Array<{ time: string; artist: string }>) {
  const byTime = new Map<string, { time: string; artist: string }>();
  for (const item of base) byTime.set(String(item.time), { time: String(item.time), artist: String(item.artist || "") });
  for (const item of updates) byTime.set(String(item.time), { time: String(item.time), artist: String(item.artist || "") });
  return sortShowtime(Array.from(byTime.values()));
}

function minutes(t: string): number {
  const [h, m] = startHhmm(t)?.split(":").map(Number) || [99, 0];
  return h < 9 ? h * 60 + m + 24 * 60 : h * 60 + m;
}

function startHhmm(t: string): string | null {
  const m = String(t || "").split("-")[0].trim().replace(".", ":").match(/^(\d{1,2}):(\d{2})$/);
  return m ? `${String(Number(m[1])).padStart(2, "0")}:${m[2]}` : null;
}

function formatShowtimeItems(items: Array<{ time: string; artist: string }>): string {
  return items.map((x) => `${x.time} | ${x.artist}`).join("\n");
}

async function saveShowtime(env: Env, eventName: string, showDate: string, items: Array<{ time: string; artist: string }>): Promise<void> {
  await supabaseUpsert(env, "showtime_events", { event_name: eventName, show_date: showDate || null }, "event_name");
  await supabaseDelete(env, "showtimes", [`event_name=eq.${encodeURIComponent(eventName)}`]);
  for (const item of items) await supabaseInsert(env, "showtimes", { event_name: eventName, time: item.time, artist: item.artist });
}

async function loadShowtime(env: Env, eventName: string): Promise<{ show_date?: string; schedule: Array<{ time: string; artist: string }> }> {
  const meta = await supabaseSelect<any>(env, "showtime_events", "*", [`event_name=eq.${encodeURIComponent(eventName)}`], "limit=1");
  const rows = await supabaseSelect<any>(env, "showtimes", "*", [`event_name=eq.${encodeURIComponent(eventName)}`]);
  return { show_date: meta[0]?.show_date, schedule: sortShowtime(rows.map((r) => ({ time: r.time, artist: r.artist }))) };
}

async function listShowtimeEvents(env: Env): Promise<Array<{ event_name: string; count: number; show_date?: string }>> {
  const rows = await supabaseSelect<any>(env, "showtimes", "event_name");
  const meta = await supabaseSelect<any>(env, "showtime_events", "event_name,show_date");
  const counts: Record<string, number> = {};
  for (const row of rows) counts[row.event_name || "default"] = (counts[row.event_name || "default"] || 0) + 1;
  const names = new Set([...Object.keys(counts), ...meta.map((m) => m.event_name)]);
  return Array.from(names).sort().map((name) => ({ event_name: name, count: counts[name] || 0, show_date: meta.find((m) => m.event_name === name)?.show_date }));
}

function buildShowtimeEventList(events: Array<{ event_name: string; count: number; show_date?: string }>): string {
  return "ตาราง Showtime ที่มีอยู่:\n" + events.map((e, i) => `${i + 1}. ${e.event_name} (${e.count} วง)${e.show_date ? ` | ${e.show_date}` : ""}`).join("\n") + "\nพิมพ์ เพิ่ม เพื่อเพิ่ม event ใหม่";
}

function buildShowtimeCommandText(): string {
  return [
    "เมนู Showtime:",
    "menu showtime",
    "help showtime",
    "showtime",
    "showtime [เลข]",
    "เพิ่ม",
    "add showtime",
    "end showtime",
    "end showtime [เลข]",
    "save",
    "exit",
    "cancel",
  ].join("\n");
}

function buildShowtimeMenuFlex(): Record<string, unknown> {
  return {
    type: "flex",
    altText: "เมนู Showtime",
    contents: {
      type: "bubble",
      size: "mega",
      body: {
        type: "box",
        layout: "vertical",
        spacing: "md",
        contents: [
          { type: "text", text: "Showtime", weight: "bold", size: "xl", align: "center", color: "#7C3AED" },
          { type: "text", text: "เลือกคำสั่งที่ต้องการใช้งาน", size: "sm", color: "#777777", align: "center", wrap: true },
        ],
      },
      footer: {
        type: "box",
        layout: "vertical",
        spacing: "sm",
        flex: 0,
        contents: [
          { type: "button", style: "primary", color: "#7C3AED", height: "md", action: { type: "message", label: "ดูรายการ", text: "showtime" } },
          { type: "button", style: "secondary", height: "md", action: { type: "message", label: "เพิ่มตาราง", text: "เพิ่ม" } },
          { type: "button", style: "secondary", height: "md", action: { type: "message", label: "ดูคำสั่ง", text: "help showtime" } },
          { type: "button", style: "secondary", height: "md", action: { type: "message", label: "ลบ event", text: "end showtime" } },
          { type: "button", style: "secondary", height: "md", action: { type: "message", label: "ออก", text: "exit" } },
        ],
      },
    },
  };
}

async function buildShowtimeCard(env: Env, eventName: string): Promise<FlexNode> {
  const st = await loadShowtime(env, eventName);
  const body: FlexNode[] = [];
  if (st.schedule.length) {
    for (const it of st.schedule) body.push({ type: "text", text: `${it.time || "-"}  ${it.artist || ""}`.trim(), size: "sm", color: "#222222", wrap: true });
  } else {
    body.push({ type: "text", text: "ยังไม่มีข้อมูล Showtime", size: "sm", color: "#666666" });
  }
  return flexCard({ altText: `Showtime ${eventName}`, title: eventName, subtitle: `วันที่จัดแสดง: ${st.show_date || "ไม่ระบุ"}`, body });
}

async function formatShowtimeMessage(env: Env, eventName: string): Promise<string> {
  const st = await loadShowtime(env, eventName);
  return `วันที่จัดแสดง: ${st.show_date || "ไม่ระบุ"}\n` + (st.schedule.length ? formatShowtimeItems(st.schedule) : "ยังไม่มีข้อมูล Showtime");
}

async function deleteShowtimeEvent(env: Env, eventName: string): Promise<void> {
  await supabaseDelete(env, "showtimes", [`event_name=eq.${encodeURIComponent(eventName)}`]);
  await supabaseDelete(env, "showtime_events", [`event_name=eq.${encodeURIComponent(eventName)}`]);
}

async function createExportJob(env: Env, trip: Trip, targetId: string, requestedBy: string): Promise<any> {
  return supabaseInsert<any>(env, "export_jobs", { trip_id: String(trip.id), trip_title: trip.title, target_id: targetId, requested_by: requestedBy, status: "queued" });
}

function dispatchExportJobInBackground(ctx: ExecutionContext, env: Env, job: any): void {
  ctx.waitUntil(dispatchExportJob(env, job).catch((error) => console.error("Export dispatch failed", errorMessage(error))));
}

async function dispatchExportJob(env: Env, job: any): Promise<void> {
  if (env.GITHUB_TOKEN && env.GITHUB_REPO) {
    const workflowId = env.GITHUB_WORKFLOW_ID || "export-trip.yml";
    const ref = env.GITHUB_WORKFLOW_REF || "main";
    const res = await fetchWithTimeout(`https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${workflowId}/dispatches`, {
      method: "POST",
      headers: { Authorization: `Bearer ${env.GITHUB_TOKEN}`, "Content-Type": "application/json", "User-Agent": "line-trip-bot-worker" },
      body: JSON.stringify({ ref, inputs: { export_job_id: job.id } }),
    }, 8000);
    if (!res.ok) {
      const error = await res.text();
      await supabasePatch(env, "export_jobs", { status: "queued_dispatch_failed", error }, [`id=eq.${job.id}`]);
      await push(env, job.target_id, `Export Excel เริ่มไม่สำเร็จ\nGitHub Actions: ${res.status}\n${error.slice(0, 300)}`);
    }
    return;
  }
  await supabasePatch(env, "export_jobs", { status: "queued_dispatch_failed", error: "Missing GITHUB_TOKEN or GITHUB_REPO" }, [`id=eq.${job.id}`]);
  await push(env, job.target_id, "Export Excel เริ่มไม่สำเร็จ\nยังไม่ได้ตั้ง GITHUB_TOKEN หรือ GITHUB_REPO ใน Worker");
}

function formatSchedule(s: any) {
  return { id: String(s.id), targetId: s.target_id || "", buyerName: s.buyer_name || "", name: s.name || "", url: s.url || "", saleTime: s.sale_time || "", site: s.site || "", active: s.active ?? true, createdAt: s.created_at || "" };
}

function buildStateText(state: BotState | null): string {
  return state && SHOWTIME_ACTIONS.has(state.action) ? "ตอนนี้อยู่ในโหมด Showtime" : state ? `ตอนนี้อยู่ใน state: ${state.action}` : "ตอนนี้อยู่ในโหมดปกติ";
}

function buildHelpText(): string {
  return "คำสั่ง: ทริป, ยอด, ยอดวันนี้, edit [id] [ยอด], history, excel, end trip, showtime\nเพิ่มรายจ่าย: ผู้จ่าย #หมวด ยอด คนหาร...\nเช่น บอล #ค่าข้าว 120 บอล ปาค มิน\nสลิป: ส่งรูป แล้วพิมพ์ ผู้จ่าย #หมวด คนหาร...";
}

function buildHelpFlex(): Record<string, unknown> {
  return {
    type: "flex",
    altText: "คำสั่งทั้งหมด",
    contents: {
      type: "bubble",
      size: "mega",
      body: {
        type: "box",
        layout: "vertical",
        spacing: "md",
        contents: [
          { type: "text", text: "คำสั่งทั้งหมด", weight: "bold", size: "xl", align: "center", color: "#1DB446" },
          { type: "text", text: "เลือกคำสั่งที่ต้องการใช้งาน", size: "sm", color: "#777777", align: "center", wrap: true },
        ],
      },
      footer: {
        type: "box",
        layout: "vertical",
        spacing: "sm",
        flex: 0,
        contents: [
          { type: "button", style: "primary", color: "#1DB446", height: "md", action: { type: "message", label: "เมนูหลัก", text: "menu" } },
          { type: "button", style: "secondary", height: "md", action: { type: "message", label: "ยอดรวม", text: "ยอด" } },
          { type: "button", style: "secondary", height: "md", action: { type: "message", label: "ยอดวันนี้", text: "ยอดวันนี้" } },
          { type: "button", style: "secondary", height: "md", action: { type: "message", label: "แก้ไขรายการ", text: "edit" } },
          { type: "button", style: "secondary", height: "md", action: { type: "message", label: "ประวัติ/Excel", text: "history" } },
          { type: "button", style: "secondary", height: "md", action: { type: "message", label: "Showtime", text: "showtime" } },
        ],
      },
    },
  };
}

function buildMainMenuFlex(): Record<string, unknown> {
  return {
    type: "flex",
    altText: "เมนูคำสั่ง",
    contents: {
      type: "bubble",
      size: "mega",
      body: {
        type: "box",
        layout: "vertical",
        spacing: "md",
        contents: [
          { type: "text", text: "Trip Manager", weight: "bold", size: "xl", align: "center", color: "#1DB446" },
          { type: "text", text: "เลือกคำสั่งที่ต้องการใช้งาน", size: "sm", color: "#999999", align: "center", wrap: true },
        ],
      },
      footer: {
        type: "box",
        layout: "vertical",
        spacing: "sm",
        flex: 0,
        contents: [
          { type: "button", style: "primary", color: "#1DB446", height: "md", action: { type: "message", label: "สร้างทริป", text: "ทริป" } },
          { type: "button", style: "secondary", height: "md", action: { type: "message", label: "ยอดรวม", text: "ยอด" } },
          { type: "button", style: "secondary", height: "md", action: { type: "message", label: "แก้ไข", text: "edit" } },
          { type: "button", style: "secondary", height: "md", action: { type: "message", label: "Showtime", text: "showtime" } },
          { type: "button", style: "secondary", height: "md", action: { type: "message", label: "เมนู", text: "เมนู" } },
        ],
      },
    },
  };
}

function truncateLineText(text: string): string {
  return text.length > 4900 ? text.slice(0, 4900) + "..." : text;
}

function thaiDateString(d: Date): string {
  return new Date(d.getTime() + 7 * 60 * 60 * 1000).toISOString().slice(0, 10);
}

function thaiDateFromIso(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso.slice(0, 10) : thaiDateString(d);
}

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json; charset=utf-8" } });
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
