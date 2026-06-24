const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function loadParser() {
  const source = fs.readFileSync(path.join(__dirname, "..", "public", "app.js"), "utf8");
  const element = () => ({
    value: "",
    textContent: "",
    innerHTML: "",
    classList: { add() {}, remove() {} },
    addEventListener() {},
    replaceChildren() {},
    append() {},
    reset() {},
    querySelector() { return null; },
  });
  const sandbox = {
    console,
    Intl,
    Date,
    URLSearchParams,
    FormData: function FormData() {},
    fetch: async () => ({ ok: true, status: 200, json: async () => ({ schedules: [] }) }),
    setInterval() {},
    setTimeout() {},
    clearInterval() {},
    clearTimeout() {},
    localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    history: { replaceState() {} },
    location: { hash: "", pathname: "/", search: "" },
    window: { open() {} },
    document: {
      getElementById() { return element(); },
      createElement() { return element(); },
      querySelector() { return null; },
      addEventListener() {},
    },
    confirm() { return true; },
  };
  vm.createContext(sandbox);
  vm.runInContext(`${source}\n;globalThis.__parseDateTimeFromText = parseDateTimeFromText;`, sandbox);
  return sandbox.__parseDateTimeFromText;
}

const parseDateTimeFromText = loadParser();

test("parses Thai month text with explicit minutes", () => {
  const parsed = parseDateTimeFromText("PUBLIC SALE : 30 พฤษภาคม 2569 | เวลา 10.00 น. เป็นต้นไป");
  assert.ok(parsed instanceof Date);
  assert.equal(parsed.toISOString(), "2026-05-30T03:00:00.000Z");
});

test("parses Thai month text when source only includes hour", () => {
  const parsed = parseDateTimeFromText("SALE : 29 พฤษภาคม 2569 | เวลา 10");
  assert.ok(parsed instanceof Date);
  assert.equal(parsed.toISOString(), "2026-05-29T03:00:00.000Z");
});
