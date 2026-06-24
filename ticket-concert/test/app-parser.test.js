const test = require("node:test");
const assert = require("node:assert/strict");
const { parseDateTimeFromText } = require("../public/app");

test("parses Thai month text with hour and minute", () => {
  const parsed = parseDateTimeFromText("PUBLIC SALE : 30 พฤษภาคม 2569 | เวลา 10.00 น. เป็นต้นไป");
  assert.ok(parsed instanceof Date);
  assert.equal(parsed.toISOString(), "2026-05-30T03:00:00.000Z");
});

test("parses Thai month text with hour only", () => {
  const parsed = parseDateTimeFromText("จำหน่ายบัตรรอบ Early Ghost ตั้งแต่วันที่ 27 มิถุนายน 2569 เวลา 10:00 น.");
  assert.ok(parsed instanceof Date);
  assert.equal(parsed.toISOString(), "2026-06-27T03:00:00.000Z");
});

test("parses Thai abbreviated month text", () => {
  const parsed = parseDateTimeFromText("25 มิ.ย. 2569 เวลา 05:00 น");
  assert.ok(parsed instanceof Date);
  assert.equal(parsed.toISOString(), "2026-06-24T22:00:00.000Z");
});
