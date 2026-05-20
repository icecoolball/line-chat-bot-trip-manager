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
// [อัปเดตล่าสุด 2026-05-20]: แปลงรูปแบบวันเวลาเป็น dd-mm-yyyy HH:MM:SS.ms
//    🔧 เปลี่ยนจาก yyyy-mm-dd เป็น dd-mm-yyyy ตามที่ผู้ใช้ต้องการ
// =================================================================
function formatLocalDateTime(date) {
  const pad = (value, size = 2) => String(value).padStart(size, "0");
  return `${pad(date.getDate())}-${pad(date.getMonth() + 1)}-${date.getFullYear()} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}.${pad(date.getMilliseconds(), 3)}`;
}

// แสดงแถบป็อปอัปแจ้งเตือนความจำสำเร็จขนาดสั้นที่มุมล่างขวา
function toast(message) {
  const box = $("toast");
  if (!box) return;
  box.textContent = message;
  box.classList.add("show");
  setTimeout(() => box.classList.remove("show"), 2600);
}

// ฟังก์ชันเขียนและอัปเดตหน้า Log ตรรกะเรียลไทม์
function updateStatusLogView() {
  const customName = $("eventName") ? $("eventName").value.trim() : "";
  const seatCount = $("seatCount") ? $("seatCount").value : "-";
  const primaryZone = $("primaryZone") ? $("primaryZone").value : "-";
  const eventSlug = getEventSlug();
  
  const totalPrice = Number($("totalPrice") ? $("totalPrice").value : 0);
  const countNum = Number(seatCount || 0);
  const perTicket = countNum > 0 && totalPrice > 0 ? totalPrice / countNum : 0;
  const priceText = perTicket
    ? `${perTicket.toLocaleString("th-TH", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} บาท/ใบ (รวม ${totalPrice.toLocaleString("th-TH")} บาท)`
    : "-";
  const urlValue = $("eventUrl") ? $("eventUrl").value.trim() : "-";

  const statusLog = $("statusLog");
  if (statusLog) {
    statusLog.innerHTML = `
      <div style="font-family: monospace; white-space: pre-wrap; line-height: 1.6;">1. ชื่อ : ${customName || "-"}
2. จำนวนบัตร : ${seatCount || "-"}
3. โซน : ${primaryZone || "-"}
4. ชื่องาน : ${eventSlug || "-"}
5. ราคาบัตร : ${priceText}
6. ลิงก์งาน : ${urlValue || "-"}</div>
    `;
  }
}

function log(message) {
  console.log(`[System Log] ${message}`);
}

function copyValue(id) {
  const input = $(id);
  if (!input) return;
  navigator.clipboard.writeText(input.value || "").then(() => {
    toast(`คัดลอก ${input.dataset.label} แล้ว`);
  });
}

function makeField(container, [id, label, type = "text"]) {
  if (!container) return;
  const row = document.createElement("div");
  row.className = "copyRow";
  row.innerHTML = `
    <label>${label}
      <input id="${id}" data-label="${label}" type="${type}" autocomplete="off">
    </label>
    <button type="button" class="secondary" data-copy="${id}">Copy</button>
  `;
  container.appendChild(row);
}

function renderFields() {
  const buyerContainer = $("buyerFields");
  const cardContainer = $("cardFields");
  
  if (buyerContainer) {
    buyerFields.forEach((field) => makeField(buyerContainer, field));
  }
  if (cardContainer) {
    cardFields.forEach((field) => makeField(cardContainer, field));
  }
  
  document.querySelectorAll("[data-copy]").forEach((button) => {
    button.addEventListener("click", () => copyValue(button.dataset.copy));
  });
}

function renderChecks() {}

function collectPlainState() {
  const ids = ["eventName", "site", "eventUrl", "saleTime", "seatCount", "primaryZone", "totalPrice", "backupPlan", "lineMessage", "scheduleTargetId", "scheduleBuyerName"];
  const state = {};
  ids.forEach((id) => { 
    const el = $(id);
    if (el) state[id] = el.value; 
  });
  return state;
}

function restorePlainState() {
  const state = JSON.parse(localStorage.getItem("ticketPrepState") || "{}");
  Object.entries(state).forEach(([id, value]) => {
    const el = $(id);
    if (el) el.value = value;
  });
  updateSaleTarget();
  updateStatusLogView();
}

function collectVault() {
  [...buyerFields, ...cardFields].forEach(([id]) => {
    const el = $(id);
    if (el) vault[id] = el.value;
  });
}

function fillVault() {
  const savedPlain = localStorage.getItem("ticketPrepVaultPlain");
  if (savedPlain) {
    vault = JSON.parse(savedPlain);
    [...buyerFields, ...cardFields].forEach(([id]) => {
      const el = $(id);
      if (el) el.value = vault[id] || "";
    });
  }
}

async function saveVault() {
  collectVault();
  localStorage.setItem("ticketPrepVaultPlain", JSON.stringify(vault));
}

function updateSaleTarget() {
  const saleTimeEl = $("saleTime");
  if (!saleTimeEl) return;
  
  const value = saleTimeEl.value;
  saleTargetMs = value ? new Date(value).getTime() : null;
  alarmed = false;
  
  const targetTimeNote = $("targetTimeNote");
  if (!targetTimeNote) return;
  
  if (!saleTargetMs) {
    targetTimeNote.textContent = "ยังไม่ได้เลือกเวลาเปิดขาย";
    return;
  }
  const target = new Date(saleTargetMs);
  targetTimeNote.textContent = `โปรแกรมอ่านเวลาเปิดขายเป็น ${formatLocalDateTime(target)} ตาม timezone ของเครื่อง`;
}

function formatMs(ms) {
  const sign = ms < 0 ? "-" : "";
  const abs = Math.abs(ms);
  const hours = Math.floor(abs / 3600000);
  const minutes = Math.floor(abs % 3600000 / 60000);
  const seconds = Math.floor(abs % 60000 / 1000);
  const millis = Math.floor(abs % 1000);
  return `${sign}${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
}

function beep() {
  try {
    const audio = new AudioContext();
    const osc = audio.createOscillator();
    const gain = audio.createGain();
    osc.connect(gain);
    gain.connect(audio.destination);
    osc.frequency.value = 880;
    gain.gain.setValueAtTime(0.001, audio.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.25, audio.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.001, audio.currentTime + 0.45);
    osc.start();
    osc.stop(audio.currentTime + 0.5);
  } catch(e) {
    console.log("beep error:", e);
  }
}

function tick() {
  const countdownEl = $("countdown");
  const timeStatusEl = $("timeStatus");
  
  if (!countdownEl) return;
  
  if (!saleTargetMs) {
    countdownEl.textContent = "--:--:--.---";
    return;
  }
  const now = Date.now() + offsetMs;
  const remaining = saleTargetMs - now;
  countdownEl.textContent = formatMs(remaining);
  if (timeStatusEl) {
    timeStatusEl.textContent = remaining < 0 ? "เลยเวลาเปิดขายแล้ว" : `เหลือเวลา ${formatMs(remaining)}`;
  }
  if (remaining <= 0 && !alarmed) {
    alarmed = true;
    beep();
    toast("ถึงเวลาเปิดขายแล้ว");
  }
}

async function syncTime() {
  const serverTimeNote = $("serverTimeNote");
  if (!serverTimeNote) return;
  
  serverTimeNote.textContent = "กำลังเชื่อมต่อเพื่อซิงค์เวลาเซิร์ฟเวอร์...";
  try {
    const start = Date.now();
    const res = await fetch("/", { method: "HEAD", cache: "no-cache" });
    const end = Date.now();
    const latency = (end - start) / 2;

    const serverDateStr = res.headers.get("Date");
    if (!serverDateStr) throw new Error("ไม่พบข้อมูลกำกับเวลาใน HTTP Header");

    const serverTime = new Date(serverDateStr).getTime() + latency;
    const localTime = Date.now();
    offsetMs = serverTime - localTime;

    serverTimeNote.textContent = `เทียบเวลาสำเร็จ: offset ${offsetMs} ms, latency ${Math.round(latency)} ms, Date: ${serverDateStr}`;
    const timeStatusEl = $("timeStatus");
    if (timeStatusEl) timeStatusEl.textContent = `อิงเวลาจากเซิร์ฟเวอร์หลัก offset ${offsetMs} ms`;
    toast("ซิงค์เวลาเรียบร้อยแล้ว");
  } catch (error) {
    offsetMs = 0;
    serverTimeNote.textContent = `ดึงเวลาล้มเหลวชั่วคราว: ${error.message} (สลับใช้เวลาในเครื่องคุณแทน)`;
    const timeStatusEl = $("timeStatus");
    if (timeStatusEl) timeStatusEl.textContent = "อิงเวลาปัจจุบันจากเครื่องคอมพิวเตอร์ของคุณชั่วคราว";
    toast("ดึงเวลาล้มเหลว");
  }
}

// ฟังก์ชันวิเคราะห์ภาษาและแปลงวันเวลา
// =================================================================
// [อัปเดตล่าสุด 2026-05-20]: ฟังก์ชันวิเคราะห์ภาษาและแปลงวันเวลา
//    🔧 รองรับรูปแบบไทย: "21 พฤษภาคม 2569 เวลา 09:00 น."
//    🔧 รองรับรูปแบบไทย: "21 พฤษภาคม 2569 09:00"
//    🔧 รองรับภาษาอังกฤษ: "21 May 2026 09:00"
//    🔧 รองรับตัวเลขล้วน: "2026-05-21 09:00", "21/05/2026 09:00"
//    🔧 แปลงปี พ.ศ. เป็น ค.ศ. อัตโนมัติ
// =================================================================
function parseDateTimeFromText(rawText) {
  if (!rawText) return null;

  let text = rawText.trim();
  let originalText = text;
  
  // ลบคำว่า "น." หรือ "น" ที่ท้ายข้อความ (ตัวบอกเวลาไทย)
  text = text.replace(/[นน\.]+$/g, '').trim();
  // ลบคำว่า "เวลา" ออก
  text = text.replace(/เวลา/g, ' ').trim();
  
  // แปลงตัวเลขไทยเป็นอารบิก (ถ้ามี)
  const thaiNumbers = {
    '๐': '0', '๑': '1', '๒': '2', '๓': '3', '๔': '4',
    '๕': '5', '๖': '6', '๗': '7', '๘': '8', '๙': '9'
  };
  text = text.replace(/[๐-๙]/g, (match) => thaiNumbers[match]);

  // =================================================================
  // รูปแบบที่ 1: ภาษาไทย พ.ศ. เช่น "21 พฤษภาคม 2569 เวลา 09:00"
  // =================================================================
  const thaiMonthPattern = /(\d{1,2})\s+(มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|กรกฎาคม|สิงหาคม|กันยายน|ตุลาคม|พฤศจิกายน|ธันวาคม|ม\.ค\.|ก\.พ\.|มี\.ค\.|เม\.ย\.|พ\.ค\.|มิ\.ย\.|ก\.ค\.|ส\.ค\.|ก\.ย\.|ต\.ค\.|พ\.ย\.|ธ\.ค\.|มค|กพ|มีค|เมย|พค|มิย|กค|สค|กย|ตค|พย|ธค)\s+(\d{3,4})(?:\s+)?(\d{1,2}:\d{2})?/i;
  
  const thaiMatch = text.match(thaiMonthPattern);
  
  if (thaiMatch) {
    let day = parseInt(thaiMatch[1], 10);
    let thaiMonth = thaiMatch[2].toLowerCase();
    let yearBE = parseInt(thaiMatch[3], 10);
    let timeStr = thaiMatch[4] || "";
    
    // แปลงปี พ.ศ. เป็น ค.ศ.
    let yearCE = yearBE;
    if (yearBE > 2500) yearCE = yearBE - 543;
    if (yearBE < 100) yearCE = yearBE + 2000;
    
    // แปลงชื่อเดือนไทยเป็นตัวเลขเดือน
    const monthMap = {
      "มกราคม": 0, "ม.ค.": 0, "มค": 0,
      "กุมภาพันธ์": 1, "ก.พ.": 1, "กพ": 1,
      "มีนาคม": 2, "มี.ค.": 2, "มีค": 2,
      "เมษายน": 3, "เม.ย.": 3, "เมย": 3,
      "พฤษภาคม": 4, "พ.ค.": 4, "พค": 4,
      "มิถุนายน": 5, "มิ.ย.": 5, "มิย": 5,
      "กรกฎาคม": 6, "ก.ค.": 6, "กค": 6,
      "สิงหาคม": 7, "ส.ค.": 7, "สค": 7,
      "กันยายน": 8, "ก.ย.": 8, "กย": 8,
      "ตุลาคม": 9, "ต.ค.": 9, "ตค": 9,
      "พฤศจิกายน": 10, "พ.ย.": 10, "พย": 10,
      "ธันวาคม": 11, "ธ.ค.": 11, "ธค": 11
    };
    
    let month = monthMap[thaiMonth];
    if (month === undefined) month = 0;
    
    let hours = 10, minutes = 0, seconds = 0;
    
    if (timeStr) {
      const timeMatch = timeStr.match(/(\d{1,2}):(\d{2})/);
      if (timeMatch) {
        hours = parseInt(timeMatch[1], 10);
        minutes = parseInt(timeMatch[2], 10);
      }
    } else {
      // หาเวลาในรูปแบบ HH:MM จากข้อความทั้งหมด
      const timeAnywhere = text.match(/(\d{1,2}):(\d{2})/);
      if (timeAnywhere) {
        hours = parseInt(timeAnywhere[1], 10);
        minutes = parseInt(timeAnywhere[2], 10);
      }
    }
    
    const finalDate = new Date(yearCE, month, day, hours, minutes, seconds);
    
    if (isNaN(finalDate.getTime())) {
      return null;
    }
    
    return {
      finalDate,
      isoLocal: `${finalDate.getFullYear()}-${String(finalDate.getMonth() + 1).padStart(2, "0")}-${String(finalDate.getDate()).padStart(2, "0")}T${String(finalDate.getHours()).padStart(2, "0")}:${String(finalDate.getMinutes()).padStart(2, "0")}:${String(finalDate.getSeconds()).padStart(2, "0")}`,
      matchedText: originalText.substring(0, 60)
    };
  }
  
  // =================================================================
  // รูปแบบที่ 2: ภาษาอังกฤษ เช่น "21 May 2026 09:00"
  // =================================================================
  const enMonthPattern = /(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\s+(\d{2,4})(?:\s+)?(\d{1,2}:\d{2})?/i;
  const enMatch = text.match(enMonthPattern);
  
  if (enMatch) {
    let day = parseInt(enMatch[1], 10);
    let monthStr = enMatch[2].toLowerCase();
    let year = parseInt(enMatch[3], 10);
    let timeStr = enMatch[4] || "";
    
    if (year < 100) year += 2000;
    if (year > 2500) year -= 543;
    
    const monthMap = {
      "january": 0, "jan": 0, "february": 1, "feb": 1, "march": 2, "mar": 2,
      "april": 3, "apr": 3, "may": 4, "june": 5, "jun": 5, "july": 6, "jul": 6,
      "august": 7, "aug": 7, "september": 8, "sep": 8, "october": 9, "oct": 9,
      "november": 10, "nov": 10, "december": 11, "dec": 11
    };
    
    let month = monthMap[monthStr];
    if (month === undefined) month = 0;
    
    let hours = 10, minutes = 0, seconds = 0;
    
    if (timeStr) {
      const timeMatch = timeStr.match(/(\d{1,2}):(\d{2})/);
      if (timeMatch) {
        hours = parseInt(timeMatch[1], 10);
        minutes = parseInt(timeMatch[2], 10);
      }
    } else {
      const timeAnywhere = text.match(/(\d{1,2}):(\d{2})/);
      if (timeAnywhere) {
        hours = parseInt(timeAnywhere[1], 10);
        minutes = parseInt(timeAnywhere[2], 10);
      }
    }
    
    const finalDate = new Date(year, month, day, hours, minutes, seconds);
    
    if (isNaN(finalDate.getTime())) {
      return null;
    }
    
    return {
      finalDate,
      isoLocal: `${finalDate.getFullYear()}-${String(finalDate.getMonth() + 1).padStart(2, "0")}-${String(finalDate.getDate()).padStart(2, "0")}T${String(finalDate.getHours()).padStart(2, "0")}:${String(finalDate.getMinutes()).padStart(2, "0")}:${String(finalDate.getSeconds()).padStart(2, "0")}`,
      matchedText: originalText.substring(0, 60)
    };
  }
  
  // =================================================================
  // รูปแบบที่ 3: ตัวเลขล้วน เช่น "2026-05-21 09:00" หรือ "21/05/2026 09:00"
  // =================================================================
  const dateTimeMatch = text.match(/(\d{1,4})[-/.](\d{1,2})[-/.](\d{1,4})(?:\s+)?(\d{1,2}:\d{2})?/);
  if (dateTimeMatch) {
    let p1 = parseInt(dateTimeMatch[1], 10);
    let p2 = parseInt(dateTimeMatch[2], 10);
    let p3 = parseInt(dateTimeMatch[3], 10);
    let timeStr = dateTimeMatch[4] || "";
    
    let year, month, day;
    
    if (p1 > 31) {
      year = p1; month = p2 - 1; day = p3;
    } else {
      day = p1; month = p2 - 1; year = p3;
    }
    
    if (year > 2500) year -= 543;
    if (year < 100) year += 2000;
    
    let hours = 10, minutes = 0, seconds = 0;
    
    if (timeStr) {
      const timeMatch = timeStr.match(/(\d{1,2}):(\d{2})/);
      if (timeMatch) {
        hours = parseInt(timeMatch[1], 10);
        minutes = parseInt(timeMatch[2], 10);
      }
    } else {
      const timeAnywhere = text.match(/(\d{1,2}):(\d{2})/);
      if (timeAnywhere) {
        hours = parseInt(timeAnywhere[1], 10);
        minutes = parseInt(timeAnywhere[2], 10);
      }
    }
    
    const finalDate = new Date(year, month, day, hours, minutes, seconds);
    
    if (isNaN(finalDate.getTime())) {
      return null;
    }
    
    return {
      finalDate,
      isoLocal: `${finalDate.getFullYear()}-${String(finalDate.getMonth() + 1).padStart(2, "0")}-${String(finalDate.getDate()).padStart(2, "0")}T${String(finalDate.getHours()).padStart(2, "0")}:${String(finalDate.getMinutes()).padStart(2, "0")}:${String(finalDate.getSeconds()).padStart(2, "0")}`,
      matchedText: originalText.substring(0, 60)
    };
  }
  
  return null;
}

async function detectSaleTime() {
  const eventUrl = $("eventUrl");
  if (!eventUrl) return;
  
  const urlValue = eventUrl.value.trim();
  if (!urlValue) {
    toast("กรุณาใส่ URL งานก่อน");
    return;
  }
  
  const detectedTimeNote = $("detectedTimeNote");
  if (detectedTimeNote) detectedTimeNote.textContent = "กำลังอ่านวัน/เวลาเปิดขายจากหน้าเว็บ...";
  
  try {
    const res = await fetch(`/api/event-time?url=${encodeURIComponent(urlValue)}`);
    const data = await res.json();
    if (!data.ok) throw new Error(data.error);

    const parsed = parseDateTimeFromText(data.matchedText || data.isoLocal);
    if (!parsed) throw new Error("ไม่สามารถประมวลผลรูปแบบวันที่ได้");

    const saleTimeEl = $("saleTime");
    if (saleTimeEl) saleTimeEl.value = parsed.isoLocal;
    updateSaleTarget();
    if (detectedTimeNote) {
      detectedTimeNote.textContent = `พบเวลา: ${formatLocalDateTime(parsed.finalDate)} จากข้อความ "${parsed.matchedText}"`;
    }
  } catch (error) {
    if (detectedTimeNote) {
      detectedTimeNote.textContent = `ดึงเวลาเปิดขายจาก URL ไม่ได้: ${error.message}`;
    }
    toast("หาเวลาเปิดขายจาก URL ไม่เจอ");
  }
}

async function detectSaleTimeFromText() {
  const eventText = $("eventText");
  if (!eventText) return;
  
  const text = eventText.value.trim();
  if (!text) {
    toast("กรุณาวางข้อความจากหน้าเว็บก่อน");
    return;
  }
  
  const detectedTimeNote = $("detectedTimeNote");
  if (detectedTimeNote) detectedTimeNote.textContent = "กำลังวิเคราะห์โครงสร้างภาษาไทย/อังกฤษจากข้อความ...";
  
  try {
    const parsed = parseDateTimeFromText(text);
    if (!parsed) throw new Error("ไม่พบโครงสร้างวันเวลาที่ระบุ");

    const saleTimeEl = $("saleTime");
    if (saleTimeEl) saleTimeEl.value = parsed.isoLocal;
    updateSaleTarget();
    if (detectedTimeNote) {
      detectedTimeNote.textContent = `พบเวลา: ${formatLocalDateTime(parsed.finalDate)} จากข้อความ "${parsed.matchedText}"`;
    }
  } catch (error) {
    if (detectedTimeNote) {
      detectedTimeNote.textContent = `ดึงเวลาจากข้อความไม่ได้: ${error.message}`;
    }
    toast("หาเวลาในข้อความไม่เจอ");
  }
}

function openSite() {
  const eventUrl = $("eventUrl");
  if (!eventUrl) return;
  
  const eventUrlValue = eventUrl.value.trim();
  if (!eventUrlValue) {
    toast("กรุณาใส่ URL งานก่อน");
    return;
  }
  window.open(eventUrlValue, "_blank", "noopener,noreferrer");
}

function getEventSlug() {
  const eventUrl = $("eventUrl");
  if (!eventUrl) return "";
  
  const eventUrlValue = eventUrl.value.trim();
  if (!eventUrlValue) return "";
  try {
    const urlObj = new URL(eventUrlValue);
    const pathname = urlObj.pathname;
    const match = pathname.match(/\/([^/]+)\/?$/);
    return match ? match[1] : "";
  } catch {
    const cleaned = eventUrlValue.split('?')[0];
    const match = cleaned.match(/\/([^/]+)\/?$/);
    return match ? match[1] : cleaned;
  }
}

function buildSummaryMessage() {
  const customName = $("eventName") ? $("eventName").value.trim() : "";
  const seatCount = $("seatCount") ? $("seatCount").value : "0";
  const totalPriceVal = $("totalPrice") ? $("totalPrice").value : "0";
  const totalPrice = Number(totalPriceVal || 0);
  const countNum = Number(seatCount || 0);
  const perTicket = countNum > 0 && totalPrice > 0 ? totalPrice / countNum : 0;
  const priceText = perTicket
    ? `${perTicket.toLocaleString("th-TH", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} บาท/ใบ (รวม ${totalPrice.toLocaleString("th-TH")} บาท)`
    : "-";

  const seatCountEl = $("seatCount");
  const primaryZoneEl = $("primaryZone");
  const eventUrlEl = $("eventUrl");
  
  return [
    `1. ชื่อ : ${customName || "-"}`,
    `2. จำนวนบัตร : ${seatCountEl ? seatCountEl.value : "-"}`,
    `3. โซน : ${primaryZoneEl ? primaryZoneEl.value : "-"}`,
    `4. ชื่องาน : ${getEventSlug() || "-"}`,
    `5. ราคาบัตร : ${priceText}`,
    `6. ลิงก์งาน : ${eventUrlEl ? eventUrlEl.value.trim() : "-"}`
  ].join("\n");
}

// =================================================================
// [อัปเดตล่าสุด 2026-05-20]: เพิ่ม field site เพื่อส่งค่าเว็บไซต์ที่เลือก
// =================================================================
function schedulePayload() {
  const scheduleTargetId = $("scheduleTargetId");
  const scheduleBuyerName = $("scheduleBuyerName");
  const eventName = $("eventName");
  const seatCount = $("seatCount");
  const primaryZone = $("primaryZone");
  const eventUrl = $("eventUrl");
  const totalPrice = $("totalPrice");
  const saleTime = $("saleTime");
  const site = $("site");
  
  return {
    targetId: scheduleTargetId ? scheduleTargetId.value.trim() : "",
    buyerName: scheduleBuyerName ? (scheduleBuyerName.value.trim() || (eventName ? eventName.value.trim() : "")) : "",
    seatCount: seatCount ? seatCount.value : "",
    zone: primaryZone ? primaryZone.value : "",
    name: getEventSlug(),
    url: eventUrl ? eventUrl.value.trim() : "",
    totalPrice: totalPrice ? totalPrice.value : "",
    saleTime: saleTime ? saleTime.value : "",
    site: site ? site.value : ""  // ✅ เพิ่ม site
  };
}

function formatScheduleTime(saleTime) {
  if (!saleTime) return "ไม่ระบุ";
  const date = new Date(saleTime);
  if (isNaN(date.getTime())) return "รูปแบบเวลาไม่ถูกต้อง";
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")} ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

async function loadSchedules() {
  const list = $("scheduleList");
  if (!list) return;
  try {
    const res = await fetch("/api/schedules");
    const data = await res.json();
    if (!data.ok) throw new Error(data.error);
    if (!data.schedules.length) {
      list.innerHTML = "ยังไม่มี schedule";
      return;
    }
    list.innerHTML = data.schedules.map((schedule) => {
      const sent = (schedule.reminders || []).filter((item) => item.sentAt).map((item) => item.label).join(", ") || "-";
      return `
        <div class="scheduleItem">
          <strong>${schedule.name || "-"}</strong><br>
          เวลาเปิดขาย: ${formatScheduleTime(schedule.saleTime)}<br>
          เว็บไซต์: ${schedule.site || "-"}<br>
          Target: ${schedule.targetId || "-"}<br>
          ลิงก์: ${schedule.url || "-"}<br>
          ส่งแล้ว: ${sent}<br>
          <button type="button" class="secondary" data-delete-schedule="${schedule.id}">ลบ</button>
        </div>
      `;
    }).join("");
    document.querySelectorAll("[data-delete-schedule]").forEach((button) => {
      button.addEventListener("click", async () => {
        await fetch(`/api/schedules/${encodeURIComponent(button.dataset.deleteSchedule)}`, { method: "DELETE" });
        loadSchedules();
      });
    });
  } catch (error) {
    list.textContent = `โหลด schedule ไม่ได้: ${error.message}`;
  }
}

async function createScheduleFromPage() {
  const payload = schedulePayload();
  
  if (!payload.targetId || !payload.saleTime || !payload.url) {
    toast("กรุณาใส่ LINE Target ID, URL งาน และเวลาเปิดขาย");
    return;
  }
  try {
    const res = await fetch("/api/schedules", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error);
    
    toast("ตั้ง Schedule สำเร็จ");
    loadSchedules();

    const summaryMsg = buildSummaryMessage();
    const noticeText = `🔔 ตั้งคร็อนคิว Schedule สำเร็จแล้ว!\n\n${summaryMsg}\n\n💻 ลิงก์ควบคุมแผงระบบ:\nhttps://ticket-concert-eliu.onrender.com`;
    
    await fetch("/api/line-push", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ 
        targetId: payload.targetId, 
        message: noticeText 
      })
    });
    
  } catch (error) {
    toast("ตั้ง Schedule ไม่สำเร็จ");
  }
}

function autoResizeTextarea(textarea) {
  if (!textarea) return;
  textarea.style.height = 'auto';
  textarea.style.height = textarea.scrollHeight + 'px';
}

function setLineSummaryMessage() {
  const lineMessage = $("lineMessage");
  if (lineMessage) {
    lineMessage.value = buildSummaryMessage();
    localStorage.setItem("ticketPrepState", JSON.stringify(collectPlainState()));
    autoResizeTextarea(lineMessage);
  }
  toast("สร้างข้อความสรุปแล้ว");
}

async function sendLine() {
  const message = $("lineMessage") ? $("lineMessage").value.trim() : "";
  const scheduleTargetId = $("scheduleTargetId");
  const targetId = scheduleTargetId ? scheduleTargetId.value.trim() : "";
  
  if (!targetId) {
    toast("❌ กรุณากรอก LINE Target ID ก่อนสั่งส่งข้อความ!");
    if (scheduleTargetId) scheduleTargetId.focus();
    return;
  }
  if (!message) {
    toast("กรุณากรอกข้อความ");
    return;
  }
  try {
    const res = await fetch("/api/line-push", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ message, targetId })
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error);
    toast("ส่ง LINE สำเร็จ");
  } catch (error) {
    toast("ส่ง LINE ไม่สำเร็จ");
  }
}

async function checkConfigStatus() {
  try {
    const res = await fetch("/api/config-status");
    const data = await res.json();
    if (!data.ok) return;
    const token = data.lineTokenConfigured ? "มี token แล้ว" : "ยังไม่มี token";
    const user = data.lineUserConfigured ? "มี user/group ID แล้ว" : "ยังไม่มี user/group ID";
    const secret = data.lineSecretConfigured ? "มี channel secret แล้ว" : "ยังไม่มี channel secret สำหรับ webhook";
    const lineConfigNote = $("lineConfigNote");
    if (lineConfigNote) {
      lineConfigNote.textContent = `LINE Messaging API: ${token}, ${user}, ${secret}`;
    }
  } catch {
    const lineConfigNote = $("lineConfigNote");
    if (lineConfigNote) {
      lineConfigNote.textContent = "LINE Notify ปิดบริการแล้ว จึงใช้ LINE Messaging API แทน";
    }
  }
}

function saveAll() {
  localStorage.setItem("ticketPrepState", JSON.stringify(collectPlainState()));
  saveVault();
  toast("บันทึกข้อมูลหน้าเว็บสำเร็จ");
}

function markPaid() {
  const lineMessage = $("lineMessage");
  if (lineMessage) {
    lineMessage.value = buildSummaryMessage();
    autoResizeTextarea(lineMessage);
  }
  toast("บันทึกสถานะสำเร็จ");
}

// =================================================================
// [คงเดิม]: เริ่มต้นผูกระบบการทำงานหลักบนหน้าเว็บบอร์ด
// =================================================================
document.addEventListener("DOMContentLoaded", () => {
  renderFields();
  renderChecks();
  restorePlainState();
  fillVault(); 
  checkConfigStatus();
  loadSchedules();
  setInterval(tick, 50);

  const saveAllBtn = $("saveAll");
  const openSiteBtn = $("openSite");
  const syncTimeBtn = $("syncTime");
  const detectSaleTimeBtn = $("detectSaleTime");
  const detectSaleTimeFromTextBtn = $("detectSaleTimeFromText");
  const sendLineBtn = $("sendLine");
  const buildLineMessageBtn = $("buildLineMessage");
  const markPaidBtn = $("markPaid");
  const saleTimeEl = $("saleTime");
  const createScheduleBtn = $("createSchedule");
  const refreshSchedulesBtn = $("refreshSchedules");
  const lineMessageEl = $("lineMessage");
  
  if (saveAllBtn) saveAllBtn.addEventListener("click", saveAll);
  if (openSiteBtn) openSiteBtn.addEventListener("click", openSite);
  if (syncTimeBtn) syncTimeBtn.addEventListener("click", syncTime);
  if (detectSaleTimeBtn) detectSaleTimeBtn.addEventListener("click", detectSaleTime);
  if (detectSaleTimeFromTextBtn) detectSaleTimeFromTextBtn.addEventListener("click", detectSaleTimeFromText);
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
  }, 150);
});