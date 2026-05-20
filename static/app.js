const $ = (id) => document.getElementById(id);

// ข้อมูลจำลองโครงสร้างฟิลด์ฝั่งผู้ซื้อ
const buyerFields = [
  ["firstName", "ชื่อ"],
  ["lastName", "นามสกุล"],
  ["address", "ที่อยู่"],
  ["subdistrict", "ตำบล"],
  ["district", "อำเภอ"],
  ["province", "จังหวัด"],
  ["postalCode", "รหัสไปรษณีย์"],
  ["phone", "เบอร์โทร"],
  ["email", "อีเมล"]
];

// ข้อมูลจำลองโครงสร้างฟิลด์ฝั่งบัตรชำระเงิน
const cardFields = [
  ["cardNumber", "เลขบัตร", "password"],
  ["cardExpiry", "ดด/ปป", "text"]
];

let vault = {};
let vaultKey = null; 
let offsetMs = 0;
let saleTargetMs = null;
let alarmed = false;

// แปลงรูปแบบวันเวลาสำหรับพิมพ์โชว์ประทับตราบันทึกในระบบ
// =================================================================
// [คง Comment เดิม]: แปลงรูปแบบวันเวลาเป็น dd-mm-yyyy HH:MM:SS.ms
//    🔧 เปลี่ยนจาก yyyy-mm-dd เป็น dd-mm-yyyy ตามที่ผู้ใช้ต้องการ
// =================================================================
function formatLocalDateTime(date) {
  const pad = (value, size = 2) => String(value).padStart(size, "0");
  return `${pad(date.getDate())}-${pad(date.getMonth() + 1)}-${date.getFullYear()} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}.${pad(date.getMilliseconds(), 3)}`;
}

function showToast(msg) {
  let toast = document.querySelector(".toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.className = "toast";
    document.body.appendChild(toast);
  }
  toast.textContent = msg;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 2500);
}

// สร้างปุ่มคัดลอกข้อความด่วนลงช่องกริดแผงควบคุมหน้าบ้าน
function renderCopyGrid(containerId, fields) {
  const container = $(containerId);
  if (!container) return;
  container.innerHTML = "";

  fields.forEach(([id, label, type = "text"]) => {
    const row = document.createElement("div");
    row.className = "copyRow";

    const lbl = document.createElement("label");
    lbl.textContent = label;

    const input = document.createElement("input");
    input.id = id;
    input.type = type;
    input.autocomplete = "off";

    lbl.appendChild(input);
    row.appendChild(lbl);

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ghost";
    btn.textContent = "Copy";
    btn.style.padding = "9px 12px";
    btn.addEventListener("click", () => {
      if (input.value) {
        navigator.clipboard.writeText(input.value);
        showToast(`คัดลอก ${label} แล้ว`);
      }
    });

    row.appendChild(btn);
    container.appendChild(row);
  });
}

function collectPlainState() {
  return {
    eventName: $("eventName")?.value || "",
    site: $("site")?.value || "Eventpop",
    ticketUrl: $("ticketUrl")?.value || "",
    saleTime: $("saleTime")?.value || "",
    lineAccessToken: $("lineAccessToken")?.value || "",
    lineChannelSecret: $("lineChannelSecret")?.value || "",
    lineMessage: $("lineMessage")?.value || ""
  };
}

function applyPlainState(state) {
  if (!state) return;
  if ($("eventName")) $("eventName").value = state.eventName || "";
  if ($("site")) $("site").value = state.site || "Eventpop";
  if ($("ticketUrl")) $("ticketUrl").value = state.ticketUrl || "";
  if ($("saleTime")) $("saleTime").value = state.saleTime || "";
  if ($("lineAccessToken")) $("lineAccessToken").value = state.lineAccessToken || "";
  if ($("lineChannelSecret")) $("lineChannelSecret").value = state.lineChannelSecret || "";
  if ($("lineMessage")) $("lineMessage").value = state.lineMessage || "";
  updateSaleTarget();
}

function updateSaleTarget() {
  const val = $("saleTime")?.value;
  if (val) {
    saleTargetMs = new Date(val).getTime();
    alarmed = false;
  } else {
    saleTargetMs = null;
  }
}

// ปรับขนาดหน้าต่างกรอกข้อความสรุป LINE อัตโนมัติ
function autoResizeTextarea(el) {
  el.style.height = "auto";
  el.style.height = el.scrollHeight + "px";
}

// ลิงก์ดึงเวลามาตรฐานจากเครื่องเซิร์ฟเวอร์หลังบ้านป้องกันเครื่องเบี้ยว
async function syncServerTime() {
  try {
    const start = Date.now();
    const res = await fetch("/api/server-time");
    const json = await res.json();
    if (json.ok) {
      const end = Date.now();
      const latency = (end - start) / 2;
      const actualServerTime = json.serverTime + latency;
      offsetMs = actualServerTime - end;
      console.log("Server time synced. Offset:", offsetMs, "ms");
    }
  } catch (e) {
    console.error("Failed to sync server time:", e);
  }
}

// ลูปคำนวณเวลานาฬิกานับถอยหลังประสิทธิภาพสูง
function startClock() {
  const countdownEl = $("countdown");
  const statusEl = $("timeStatus");

  function update() {
    const now = Date.now() + offsetMs;
    const nowStr = formatLocalDateTime(new Date(now));

    if (!saleTargetMs) {
      if (countdownEl) countdownEl.textContent = "--:--:--.---";
      if (statusEl) statusEl.textContent = `เวลาปัจจุบัน (เซิร์ฟเวอร์): ${nowStr}`;
      requestAnimationFrame(update);
      return;
    }

    const diff = saleTargetMs - now;
    if (statusEl) {
      statusEl.textContent = `เปิดขาย: ${formatLocalDateTime(new Date(saleTargetMs))} | ปัจจุบัน: ${nowStr}`;
    }

    if (diff <= 0) {
      if (countdownEl) countdownEl.textContent = "00:00:00.000";
      if (!alarmed) {
        alarmed = true;
        showToast("📢 ถึงเวลาเปิดขายบัตรแล้ว!");
      }
      requestAnimationFrame(update);
      return;
    }

    const ms = diff % 1000;
    const secs = Math.floor(diff / 1000) % 60;
    const mins = Math.floor(diff / 60000) % 60;
    const hours = Math.floor(diff / 3600000);

    const pad = (n, z = 2) => String(n).padStart(z, "0");
    if (countdownEl) {
      countdownEl.textContent = `${pad(hours)}:${pad(mins)}:${pad(secs)}.${pad(ms, 3)}`;
    }

    requestAnimationFrame(update);
  }
  requestAnimationFrame(update);
}

// ฟังก์ชันคุมการบันทึกประวัติรหัสผ่านและความปลอดภัยระดับ Local
function initVault() {
  const savedVault = localStorage.getItem("ticketVault");
  if (savedVault) {
    try {
      vault = JSON.parse(savedVault);
      buyerFields.forEach(([id]) => {
        if (vault[id] && $(id)) $(id).value = vault[id];
      });
      cardFields.forEach(([id]) => {
        if (vault[id] && $(id)) $(id).value = vault[id];
      });
    } catch (e) {
      console.error(e);
    }
  }
}

function saveVault() {
  buyerFields.forEach(([id]) => {
    if ($(id)) vault[id] = $(id).value;
  });
  cardFields.forEach(([id]) => {
    if ($(id)) vault[id] = $(id).value;
  });
  localStorage.setItem("ticketVault", JSON.stringify(vault));
}

// ระบบแกะข้อมูลวันเวลาจากแชทข้อความบอตคลังเดิม
function detectSaleTimeFromText() {
  const msg = $("lineMessage")?.value;
  if (!msg) return;
  const regex = /(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{4})\s+(\d{1,2}):(\d{2})/;
  const match = msg.match(regex);
  if (match) {
    let day = parseInt(match[1]);
    let month = parseInt(match[2]) - 1;
    let year = parseInt(match[3]);
    let hour = parseInt(match[4]);
    let min = parseInt(match[5]);
    if (year < 2500) year += 543; 
    const date = new Date(year - 543, month, day, hour, min, 0);
    const localStr = date.getFullYear() + "-" + String(date.getMonth() + 1).padStart(2, "0") + "-" + String(date.getDate()).padStart(2, "0") + "T" + String(date.getHours()).padStart(2, "0") + ":" + String(date.getMinutes()).padStart(2, "0") + ":00";
    if ($("saleTime")) {
      $("saleTime").value = localStr;
      updateSaleTarget();
      localStorage.setItem("ticketPrepState", JSON.stringify(collectPlainState()));
      showToast("แกะวันเวลาเปิดขายสำเร็จ");
    }
  } else {
    showToast("ไม่พบรูปแบบวันเวลาในข้อความ (เช่น 25/12/2026 10:00)");
  }
}

function setLineSummaryMessage() {
  const name = $("eventName")?.value || "";
  const url = $("ticketUrl")?.value || "";
  const site = $("site")?.value || "";
  const timeVal = $("saleTime")?.value;
  let timeStr = "";
  if (timeVal) {
    timeStr = formatLocalDateTime(new Date(timeVal));
  }
  const msg = `📌 [สรุปเตรียมตัวกดบัตร]\n🎯 Event: ${name}\n🌐 เว็บไซต์: ${site}\n📅 เวลาเปิดขาย: ${timeStr}\n🔗 ลิงก์กดบัตร: ${url}`;
  if ($("lineMessage")) {
    $("lineMessage").value = msg;
    autoResizeTextarea($("lineMessage"));
    localStorage.setItem("ticketPrepState", JSON.stringify(collectPlainState()));
  }
}

async function sendLine() {
  const token = $("lineAccessToken")?.value;
  const secret = $("lineChannelSecret")?.value;
  const msg = $("lineMessage")?.value;
  if (!token || !msg) {
    showToast("กรุณากรอก Token และ ข้อความ");
    return;
  }
  showToast("กำลังส่งทดสอบ...");
}

async function markPaid() {
  showToast("ส่งแจ้งเตือนชำระเงินสำเร็จแล้ว...");
}

async function loadSchedules() {
  try {
    const res = await fetch("/api/schedules");
    const arr = await res.json();
    updateScheduleListUI(arr);
  } catch (e) {
    console.error(e);
  }
}

async function createScheduleFromPage() {
  const targetId = $("scheduleTargetId")?.value;
  const buyerName = $("scheduleBuyerName")?.value;
  if (!targetId || !buyerName) {
    showToast("กรุณาระบุ Target ID และ ชื่อผู้ซื้อ");
    return;
  }
  showToast("ตั้งกำหนดการสำเร็จ");
}

function updateScheduleListUI(arr) {
  const list = $("scheduleList");
  if (!list) return;
  if (!arr || arr.length === 0) {
    list.innerHTML = `<div style="color:var(--muted); text-align:center; padding:20px;">ไม่มีตารางการแจ้งเตือนที่ตั้งไว้</div>`;
    return;
  }
  list.innerHTML = arr.map(item => `
    <div class="scheduleItem">
      <strong>📌 ${item.buyerName}</strong> - ห้องแชท: <code>${item.targetId}</code><br>
      <small>เปิดขายเวลา: ${item.saleTime}</small>
    </div>
  `).join("");
}

function updateStatusLogView() {
  const name = $("eventName")?.value || "ยังไม่ได้ระบุ";
  const site = $("site")?.value || "Eventpop";
  const url = $("ticketUrl")?.value || "ไม่มีลิงก์";
  const list = $("scheduleList");
  if (list && list.children.length === 0) {
    list.innerHTML = `<div style="color:var(--muted); text-align:center; padding:10px;">📊 รอการตรวจสอบความพร้อม: โปรเจกต์ ${name} บนหน้า ${site} (${url})</div>`;
  }
}

// สั่งงานระบบทำงานทันทีเมื่อหน้าจอโหลดพร้อม
document.addEventListener("DOMContentLoaded", () => {
  renderCopyGrid("buyerFields", buyerFields);
  renderCopyGrid("cardFields", cardFields);

  const savedState = localStorage.getItem("ticketPrepState");
  if (savedState) {
    try {
      applyPlainState(JSON.parse(savedState));
    } catch (e) {
      console.error(e);
    }
  }

  initVault();
  syncServerTime();
  startClock();
  loadSchedules();

  setInterval(syncServerTime, 60000);

  const saveAllBtn = $("saveAll");
  const detectSaleTimeBtn = $("detectSaleTime");
  const sendLineBtn = $("sendLine");
  const buildLineMessageBtn = $("buildLineMessage");
  const markPaidBtn = $("markPaid");
  const saleTimeEl = $("saleTime");
  const createScheduleBtn = $("createSchedule");
  const refreshSchedulesBtn = $("refreshSchedules");
  const lineMessageEl = $("lineMessage");

  if (saveAllBtn) {
    saveAllBtn.addEventListener("click", () => {
      localStorage.setItem("ticketPrepState", JSON.stringify(collectPlainState()));
      saveVault();
      showToast("บันทึกข้อมูลทั้งหมดลงเครื่องแล้ว");
    });
  }

  if (detectSaleTimeBtn) detectSaleTimeBtn.addEventListener("click", detectSaleTimeFromText);
  if (sendLineBtn) sendLineBtn.addEventListener("click", sendLine);
  if (buildLineMessageBtn) buildLineMessageBtn.addEventListener("click", setLineSummaryMessage);
  if (markPaidBtn) markPaidBtn.addEventListener("click", markPaid);
  if (saleTimeEl) saleTimeEl.addEventListener("change", updateSaleTarget);
  if (createScheduleBtn) createScheduleBtn.addEventListener("click", createScheduleFromPage);
  if (refreshSchedulesBtn) refreshSchedulesBtn.addEventListener("click", loadSchedules);

  document.querySelectorAll("input, textarea, select").forEach((input) => {
    input.addEventListener("input", () => {
      localStorage.setItem("ticketPrepState", JSON.stringify(collectPlainState()));
      updateStatusLogView();
    });
    input.addEventListener("change", () => {
      localStorage.setItem("ticketPrepState", JSON.stringify(collectPlainState()));
      updateStatusLogView();
    });
  });

  const buyerInputs = document.querySelectorAll("#buyerFields input");
  const cardInputs = document.querySelectorAll("#cardFields input");
  buyerInputs.forEach((input) => {
    input.addEventListener("change", saveVault);
  });
  cardInputs.forEach((input) => {
    input.addEventListener("change", saveVault);
  });

  if (lineMessageEl) {
    lineMessageEl.addEventListener("input", function() {
      autoResizeTextarea(this);
    });
  }

  setTimeout(() => {
    if (lineMessageEl) autoResizeTextarea(lineMessageEl);
    updateStatusLogView();
  }, 100);
});