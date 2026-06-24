const $ = (id) => document.getElementById(id);
const TIME_ZONE = "Asia/Bangkok";
const STORAGE_KEY = "ticketReminderEvent";

let offsetMs = 0;
let saleTargetMs = null;
let alarmed = false;

function toast(message) {
  const box = $("toast");
  box.textContent = message;
  box.classList.add("show");
  setTimeout(() => box.classList.remove("show"), 2600);
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) {
    $("appShell").classList.add("hidden");
    $("accessPanel").classList.remove("hidden");
  }
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function inviteFromFragment() {
  const value = location.hash.startsWith("#invite=") ? location.hash.slice(8) : "";
  return value ? decodeURIComponent(value) : "";
}

async function establishAccess() {
  const invite = inviteFromFragment();
  if (invite) {
    history.replaceState(null, "", `${location.pathname}${location.search}`);
    try {
      await api("/api/session/invite", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ token: invite }),
      });
    } catch (error) {
      $("accessMessage").textContent = `ลิงก์เชิญใช้ไม่ได้: ${error.message}`;
      return false;
    }
  }
  try {
    await api("/api/session");
    return true;
  } catch {
    return false;
  }
}

function saveState() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({
    eventName: $("eventName").value,
    site: $("site").value,
    eventUrl: $("eventUrl").value,
    saleTime: $("saleTime").value,
  }));
}

function restoreState() {
  try {
    const state = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    for (const id of ["eventName", "site", "eventUrl", "saleTime"]) {
      if (typeof state[id] === "string") $(id).value = state[id];
    }
  } catch {
    localStorage.removeItem(STORAGE_KEY);
  }
  updateSaleTarget();
}

function localInputValue(date) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: TIME_ZONE,
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  }).formatToParts(date).reduce((result, part) => ({ ...result, [part.type]: part.value }), {});
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second}`;
}

function parseDateTimeFromText(raw) {
  const text = String(raw || "").replace(/(\d+)(st|nd|rd|th)/gi, "$1");
  const numeric = text.match(/(\d{1,4})[-/.](\d{1,2})[-/.](\d{1,4})[^\d]{0,20}(\d{1,2})[:.](\d{2})(?::(\d{2}))?/);
  if (numeric) {
    let [, first, second, third, hour, minute, secondValue = "0"] = numeric;
    let year; let month; let day;
    if (Number(first) > 31) [year, month, day] = [first, second, third];
    else [day, month, year] = [first, second, third];
    year = Number(year) > 2500 ? Number(year) - 543 : Number(year);
    if (year < 100) year += 2000;
    const date = new Date(`${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}T${String(hour).padStart(2, "0")}:${minute}:${String(secondValue).padStart(2, "0")}+07:00`);
    return Number.isFinite(date.getTime()) ? date : null;
  }

  const months = {
    jan: 1, january: 1, "ม.ค.": 1, มกราคม: 1,
    feb: 2, february: 2, "ก.พ.": 2, กุมภาพันธ์: 2,
    mar: 3, march: 3, "มี.ค.": 3, มีนาคม: 3,
    apr: 4, april: 4, "เม.ย.": 4, เมษายน: 4,
    may: 5, "พ.ค.": 5, พฤษภาคม: 5,
    jun: 6, june: 6, "มิ.ย.": 6, มิถุนายน: 6,
    jul: 7, july: 7, "ก.ค.": 7, กรกฎาคม: 7,
    aug: 8, august: 8, "ส.ค.": 8, สิงหาคม: 8,
    sep: 9, september: 9, "ก.ย.": 9, กันยายน: 9,
    oct: 10, october: 10, "ต.ค.": 10, ตุลาคม: 10,
    nov: 11, november: 11, "พ.ย.": 11, พฤศจิกายน: 11,
    dec: 12, december: 12, "ธ.ค.": 12, ธันวาคม: 12,
  };
  const named = text.match(/(\d{1,2})\s+([A-Za-zก-๙.]+)\s+(\d{4})[^\d]{0,20}(\d{1,2})(?:[:.](\d{2}))?/i);
  if (!named || !months[named[2].toLowerCase()]) return null;
  const year = Number(named[3]) > 2500 ? Number(named[3]) - 543 : Number(named[3]);
  const minute = named[5] || "00";
  const date = new Date(`${String(year).padStart(4, "0")}-${String(months[named[2].toLowerCase()]).padStart(2, "0")}-${String(named[1]).padStart(2, "0")}T${String(named[4]).padStart(2, "0")}:${minute}:00+07:00`);
  return Number.isFinite(date.getTime()) ? date : null;
}

function updateSaleTarget() {
  const value = $("saleTime").value;
  saleTargetMs = value ? new Date(`${value}+07:00`).getTime() : null;
  alarmed = false;
  $("timeStatus").textContent = saleTargetMs ? new Intl.DateTimeFormat("th-TH", { dateStyle: "medium", timeStyle: "medium", timeZone: TIME_ZONE }).format(saleTargetMs) : "ยังไม่ได้ตั้งเวลา";
}

function tick() {
  if (!saleTargetMs) return;
  const remaining = Math.max(0, saleTargetMs - (Date.now() + offsetMs));
  const hours = Math.floor(remaining / 3600000);
  const minutes = Math.floor((remaining % 3600000) / 60000);
  const seconds = Math.floor((remaining % 60000) / 1000);
  const millis = remaining % 1000;
  $("countdown").textContent = `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
  if (remaining === 0 && !alarmed) {
    alarmed = true;
    toast("ถึงเวลาเปิดขายแล้ว");
  }
}

async function inspectSource() {
  const url = $("eventUrl").value.trim();
  if (!url) return toast("กรุณาใส่ URL งาน");
  $("sourceStatus").textContent = "กำลังตรวจเว็บต้นทาง...";
  const startedAt = Date.now();
  try {
    const data = await api("/api/source-inspect", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const endedAt = Date.now();
    if (data.sourceDate) offsetMs = new Date(data.sourceDate).getTime() - ((startedAt + endedAt) / 2);
    const parsed = parseDateTimeFromText(data.matchedText);
    if (parsed) {
      $("saleTime").value = localInputValue(parsed);
      updateSaleTarget();
      saveState();
    }
    $("sourceStatus").textContent = parsed
      ? `พบเวลาเปิดขายจาก “${data.matchedText}” · offset เว็บ ${Math.round(offsetMs)} ms`
      : `อ่านเวลา server ได้ แต่ยังหาเวลาเปิดขายไม่พบ · offset ${Math.round(offsetMs)} ms`;
  } catch (error) {
    $("sourceStatus").textContent = `ตรวจเว็บต้นทางไม่ได้: ${error.message}`;
  }
}

function parseManualText() {
  const parsed = parseDateTimeFromText($("eventText").value);
  if (!parsed) return toast("ยังอ่านวันและเวลาไม่ได้");
  $("saleTime").value = localInputValue(parsed);
  updateSaleTarget();
  saveState();
  toast("ตั้งเวลาเรียบร้อย");
}

function reminderSummary(reminders) {
  const sent = reminders.filter((item) => item.status === "sent").length;
  const failed = reminders.filter((item) => item.status === "failed").length;
  return failed ? `ส่งแล้ว ${sent}/5 · ล้มเหลว ${failed}` : `ส่งแล้ว ${sent}/5`;
}

function scheduleCard(schedule) {
  const card = document.createElement("article");
  card.className = "schedule-card";
  const title = document.createElement("h3");
  title.textContent = schedule.name;
  const meta = document.createElement("p");
  meta.textContent = `${schedule.site} · ${new Intl.DateTimeFormat("th-TH", { dateStyle: "medium", timeStyle: "short", timeZone: TIME_ZONE }).format(new Date(schedule.saleAt))}`;
  const status = document.createElement("p");
  status.className = "note";
  status.textContent = reminderSummary(schedule.reminders || []);
  const link = document.createElement("a");
  link.href = schedule.url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = "เปิดหน้าขายบัตร";
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "danger";
  remove.textContent = "ลบเตือน";
  remove.addEventListener("click", async () => {
    if (!confirm(`ลบการเตือน “${schedule.name}” หรือไม่`)) return;
    await api(`/api/schedules/${encodeURIComponent(schedule.id)}`, { method: "DELETE" });
    await loadSchedules();
  });
  card.append(title, meta, status, link, remove);
  return card;
}

async function loadSchedules() {
  const list = $("scheduleList");
  list.replaceChildren();
  try {
    const data = await api("/api/schedules");
    if (!data.schedules.length) {
      const empty = document.createElement("p");
      empty.className = "note";
      empty.textContent = "ยังไม่มีรายการเตือน";
      list.append(empty);
      return;
    }
    data.schedules.forEach((schedule) => list.append(scheduleCard(schedule)));
  } catch (error) {
    const message = document.createElement("p");
    message.className = "error";
    message.textContent = `โหลดรายการไม่ได้: ${error.message}`;
    list.append(message);
  }
}

async function createSchedule() {
  const saleAt = $("saleTime").value ? new Date(`${$("saleTime").value}+07:00`) : null;
  const payload = {
    name: $("eventName").value.trim(),
    site: $("site").value,
    url: $("eventUrl").value.trim(),
    saleAt: saleAt && saleAt.toISOString(),
  };
  try {
    await api("/api/schedules", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    toast("ตั้งเตือน LINE เรียบร้อย");
    await loadSchedules();
  } catch (error) {
    toast(`ตั้งเตือนไม่ได้: ${error.message}`);
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  if (!await establishAccess()) {
    $("accessPanel").classList.remove("hidden");
    return;
  }
  $("appShell").classList.remove("hidden");
  restoreState();
  await loadSchedules();
  setInterval(tick, 50);

  for (const id of ["eventName", "site", "eventUrl", "saleTime"]) {
    $(id).addEventListener("change", () => { saveState(); updateSaleTarget(); });
  }
  $("inspectSource").addEventListener("click", inspectSource);
  $("parseText").addEventListener("click", parseManualText);
  $("openSite").addEventListener("click", () => {
    const url = $("eventUrl").value.trim();
    if (url) window.open(url, "_blank", "noopener,noreferrer");
  });
  $("createSchedule").addEventListener("click", createSchedule);
  $("refreshSchedules").addEventListener("click", loadSchedules);
});
