import { normalizeCurrencyCode } from "./currency-by-country";

export type Deposit = {
  expense_id: number; trip_id: number; payer_name: string; receiver_name: string;
  amount_minor: number; currency: string; event_id?: string; event_time?: number;
};

export const DEPOSIT_HELP = "มัดจำ = ยอดสะสมที่จ่ายแล้วต่อคน ไม่ใช่รายจ่ายเพิ่ม\nดูยอด: มัดจำ [ID]\nบันทึก: มัดจำ [ID] [ยอดสะสมต่อคน] [ชื่อคนจ่าย...] ให้ [ชื่อผู้รับ]\nเช่น มัดจำ 0206 560 บอล ปาค มิน ให้ ผู้รับ\nใช้สกุลเงินเดียวกับรายการ · พิมพ์ยอด 0 เพื่อล้างยอดของคนนั้น\nหากผู้จ่ายกับผู้รับเป็นคนเดียวกัน หมายถึงเงินส่วนตัวที่กันไว้";

type DepositCommand = { id: number; amountMinor?: number; names?: string[]; receiver?: string };
export function parseDepositCommand(text: string): DepositCommand | null {
  const parts = text.trim().split(/\s+/);
  if (!/^(?:มัดจำ|deposit)$/i.test(parts[0]) || !/^\d+$/.test(parts[1] || "")) return null;
  const id = Number(parts[1]);
  if (!Number.isSafeInteger(id) || id < 1 || id > 2147483647) return null;
  if (parts.length === 2) return { id };
  const amount = parts[2] || "";
  if (!/^(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?$/.test(amount)) return null;
  const [whole,fraction=""] = amount.replace(/,/g,"").split(".");
  const amountMinor = Number(whole)*100+Number(fraction.padEnd(2,"0"));
  if (!Number.isSafeInteger(amountMinor) || amountMinor > 100000000 || parts.length < 6 || parts.at(-2) !== "ให้") return null;
  const names = parts.slice(3,-2);
  if (new Set(names).size !== names.length || names.includes("ให้")) return null;
  return {id,amountMinor,names,receiver:parts.at(-1)!};
}

export function depositPeople(raw: unknown, payer: string): string[] {
  const values = Array.isArray(raw) ? raw : String(raw || "").split(/\s+/);
  const names = [...new Set(values.map(String).map(x=>x.trim()).filter(Boolean))];
  return names.length ? names : payer ? [payer] : [];
}

export function buildDepositStatus(exp: { id: number; amount: number; currency?: string; payer_name?: string; participants?: unknown; tag?: string; item_name?: string }, deposits: Deposit[]): string {
  const currency = normalizeCurrencyCode(exp.currency) || "THB";
  deposits.forEach(d=>validateDeposit(d,currency));
  const names = depositPeople(exp.participants,exp.payer_name || "");
  const totalMinor = Math.round(Number(exp.amount)*100);
  const recorded = new Map(deposits.map(d=>[d.payer_name,d]));
  const totalPaid = deposits.reduce((sum,d)=>sum+Number(d.amount_minor),0);
  const money = (minor:number)=>(minor/100).toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2});
  const lines = [
    `มัดจำ ID ${String(exp.id).padStart(4,"0")} ${exp.tag || exp.item_name || ""}`,
    `ค่ารายการ ${money(totalMinor)} ${currency}`,
    `จ่าย/กันเงินไว้แล้ว ${money(totalPaid)} ${currency}`,
    totalPaid <= totalMinor ? `ยังขาดรวม ${money(totalMinor-totalPaid)} ${currency}` : `เกินยอดรายการ ${money(totalPaid-totalMinor)} ${currency}`,
    "เทียบกับส่วนแบ่งเต็มต่อคน (ไม่ได้ตั้งเป้ามัดจำ 50%)",
  ];
  // Allocate the last cents in participant order so displayed shares sum to the bill.
  names.forEach((name,index)=>{
    const due = Math.floor(totalMinor/names.length)+(index<totalMinor%names.length?1:0);
    const d = recorded.get(name);
    const paid = Number(d?.amount_minor || 0);
    const balance = due-paid;
    const state = !paid ? "ยังไม่จ่าย" : paid<due ? "จ่ายบางส่วน" : paid===due ? "ครบ" : "เกิน";
    lines.push(`${name}: ${state} · จ่าย ${money(paid)} · ${balance<0?"เกิน":"ค้าง"} ${money(Math.abs(balance))}${d && paid ? ` · ให้ ${d.receiver_name}` : ""}`);
  });
  return lines.join("\n");
}

// A transfer changes who has funded the bill, never the total expense or share.
export function applyDepositTransfers(paid: Record<string,number>, deposits: Deposit[], currency: string, rate: number): void {
  for (const d of deposits) {
    validateDeposit(d,currency);
    if (d.payer_name === d.receiver_name) continue;
    const amount = Number(d.amount_minor)/100*rate;
    paid[d.payer_name] = (paid[d.payer_name] || 0)+amount;
    paid[d.receiver_name] = (paid[d.receiver_name] || 0)-amount;
  }
}

function validateDeposit(deposit: Deposit, currency: string): void {
  if ((normalizeCurrencyCode(deposit.currency) || "THB") !== (normalizeCurrencyCode(currency) || "THB") || !Number.isSafeInteger(Number(deposit.amount_minor)) || Number(deposit.amount_minor)<0) {
    throw new Error("ข้อมูลมัดจำหรือสกุลเงินไม่ตรงกับรายการ");
  }
}
