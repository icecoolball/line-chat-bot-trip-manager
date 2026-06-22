# Country-Based Trip Currency + Live THB Summaries — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users start a trip by typing a country (Thai/English) or currency code, and show ยอดวันนี้/ยอดรวม/จบทริป with per-currency amounts plus live THB conversion.

**Architecture:** A new pure data module `src/currency-by-country.ts` maps countries→ISO 4217 and holds fallback rates. `worker.ts` gains `resolveBaseCurrency` (country/code → currency) and `getRateThb` (Supabase-cached, live-fetched THB rate). Trip creation and the three summary builders are rewired to use them. Live rates are cached in a new Supabase table `fx_rates`.

**Tech Stack:** TypeScript, Cloudflare Workers (wrangler), Supabase REST, vitest (new), er-api FX API.

**Spec:** `docs/superpowers/specs/2026-06-22-trip-country-currency-design.md`

---

## File Structure

- **Create** `src/currency-by-country.ts` — pure data + helpers (`COUNTRY_TO_CURRENCY`, `ISO_4217`, `FALLBACK_RATES`, `normalizeCountryName`, `normalizeCurrencyCode`). No imports, no side effects.
- **Create** `test/currency-by-country.test.ts` — unit tests for the data module.
- **Create** `test/worker-currency.test.ts` — unit tests for `resolveBaseCurrency` + `getRateThb` (mocked `fetch`).
- **Create** `vitest.config.ts` — test config.
- **Create** `db/2026-06-22-fx_rates.sql` — migration for the cache table.
- **Modify** `package.json` — add vitest devDep + `test` script.
- **Modify** `src/worker.ts` — import data module; add `resolveBaseCurrency`, `getRateThb`, `getRatesForCurrencies`; rewire prompts, `handleTripCurrency`, `getTripBaseCurrency`, `parseExpense` default, `buildTodayMessage`, `buildTripTotalMessage`, `buildEndTripSummary`.

---

## Task 1: Test infrastructure (vitest)

**Files:**
- Modify: `package.json`
- Create: `vitest.config.ts`

- [ ] **Step 1: Add vitest dev dependency**

Run: `npm install -D vitest`
Expected: `vitest` appears in `package.json` devDependencies, install succeeds.

- [ ] **Step 2: Add test script to package.json**

In `package.json` `"scripts"`, add:

```json
"test": "vitest run",
"test:watch": "vitest"
```

- [ ] **Step 3: Create vitest config**

Create `vitest.config.ts`:

```ts
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["test/**/*.test.ts"],
    environment: "node",
  },
});
```

- [ ] **Step 4: Verify the runner works (no tests yet)**

Run: `npm test`
Expected: vitest runs and reports "No test files found" (exit 0) or runs 0 tests. This confirms the runner is wired.

- [ ] **Step 5: Commit**

```bash
git add package.json package-lock.json vitest.config.ts
git commit -m "test: add vitest runner"
```

---

## Task 2: Pure data module `src/currency-by-country.ts`

**Files:**
- Create: `src/currency-by-country.ts`
- Test: `test/currency-by-country.test.ts`

- [ ] **Step 1: Write failing tests**

Create `test/currency-by-country.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import {
  COUNTRY_TO_CURRENCY,
  ISO_4217,
  FALLBACK_RATES,
  normalizeCountryName,
  normalizeCurrencyCode,
} from "../src/currency-by-country";

describe("normalizeCountryName", () => {
  it("lowercases, trims, collapses spaces", () => {
    expect(normalizeCountryName("  Japan  ")).toBe("japan");
    expect(normalizeCountryName("South   Korea")).toBe("south korea");
  });
  it("strips leading 'ประเทศ' and 'the'", () => {
    expect(normalizeCountryName("ประเทศญี่ปุ่น")).toBe("ญี่ปุ่น");
    expect(normalizeCountryName("the United States")).toBe("united states");
  });
});

describe("normalizeCurrencyCode", () => {
  it("uppercases", () => expect(normalizeCurrencyCode("jpy")).toBe("JPY"));
  it("fixes common typos/symbols", () => {
    expect(normalizeCurrencyCode("JYP")).toBe("JPY");
    expect(normalizeCurrencyCode("¥")).toBe("JPY");
    expect(normalizeCurrencyCode("₩")).toBe("KRW");
    expect(normalizeCurrencyCode("฿")).toBe("THB");
    expect(normalizeCurrencyCode("$")).toBe("USD");
  });
  it("returns empty string for nullish", () => {
    expect(normalizeCurrencyCode(null)).toBe("");
  });
});

describe("COUNTRY_TO_CURRENCY", () => {
  it("maps Thai and English names to ISO currency", () => {
    expect(COUNTRY_TO_CURRENCY["ญี่ปุ่น"]).toBe("JPY");
    expect(COUNTRY_TO_CURRENCY["japan"]).toBe("JPY");
    expect(COUNTRY_TO_CURRENCY["เกาหลี"]).toBe("KRW");
    expect(COUNTRY_TO_CURRENCY["ไทย"]).toBe("THB");
    expect(COUNTRY_TO_CURRENCY["usa"]).toBe("USD");
    expect(COUNTRY_TO_CURRENCY["ฝรั่งเศส"]).toBe("EUR");
  });
  it("every value is a valid ISO_4217 code", () => {
    for (const code of Object.values(COUNTRY_TO_CURRENCY)) {
      expect(ISO_4217.has(code)).toBe(true);
    }
  });
});

describe("ISO_4217 / FALLBACK_RATES", () => {
  it("contains the original four", () => {
    for (const c of ["THB", "JPY", "USD", "KRW"]) expect(ISO_4217.has(c)).toBe(true);
  });
  it("FALLBACK_RATES keys are valid currencies and THB=1", () => {
    expect(FALLBACK_RATES.THB).toBe(1);
    for (const c of Object.keys(FALLBACK_RATES)) expect(ISO_4217.has(c)).toBe(true);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test`
Expected: FAIL — cannot resolve `../src/currency-by-country`.

- [ ] **Step 3: Implement the data module**

Create `src/currency-by-country.ts`. Provide:

1. `normalizeCountryName` and `normalizeCurrencyCode` exactly as below.
2. `ISO_4217` — a `Set` of all active ISO 4217 codes. Transcribe the full active list (≈150 codes). The block below includes the codes needed by `FALLBACK_RATES` and `COUNTRY_TO_CURRENCY`; extend it with the remaining active ISO 4217 codes so that every value used anywhere is present.
3. `FALLBACK_RATES` — approximate THB rates (only a safety net; live rate overrides). Cover the common travel currencies listed.
4. `COUNTRY_TO_CURRENCY` — Thai + English + alias keys. The block below covers common travel countries with Thai names; extend with the remaining countries (English name → currency) transcribed from the canonical ISO 3166-1 ↔ 4217 table. All keys MUST be passed through `normalizeCountryName` form (lowercase/trimmed) — write them already-normalized.

```ts
const CURRENCY_ALIASES: Record<string, string> = {
  JYP: "JPY", JPN: "JPY", YEN: "JPY", "¥": "JPY",
  WON: "KRW", "₩": "KRW",
  "USD$": "USD", $: "USD",
  "฿": "THB", BAHT: "THB",
};

export function normalizeCountryName(input: string): string {
  return String(input || "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, " ")
    .replace(/^ประเทศ\s*/, "")
    .replace(/^the\s+/, "");
}

export function normalizeCurrencyCode(input: string | null | undefined): string {
  const x = String(input || "").trim().toUpperCase();
  if (!x) return "";
  return CURRENCY_ALIASES[x] || x;
}

export const ISO_4217 = new Set<string>([
  // original + common travel currencies (extend with full active ISO 4217 list)
  "THB", "JPY", "USD", "KRW", "EUR", "GBP", "CNY", "HKD", "TWD", "SGD",
  "MYR", "VND", "LAK", "KHR", "MMK", "IDR", "PHP", "INR", "AUD", "NZD",
  "CHF", "CAD", "AED", "SAR", "QAR", "TRY", "RUB", "ZAR", "BRL", "MXN",
  "SEK", "NOK", "DKK", "CZK", "PLN", "HUF", "EGP", "ILS", "MOP", "BND",
]);

export const FALLBACK_RATES: Record<string, number> = {
  THB: 1, JPY: 0.23, USD: 34.5, KRW: 0.025, EUR: 37.5, GBP: 44,
  CNY: 4.8, HKD: 4.4, TWD: 1.07, SGD: 25.5, MYR: 7.7, VND: 0.0014,
  LAK: 0.0016, KHR: 0.0085, MMK: 0.016, IDR: 0.0021, PHP: 0.6,
  INR: 0.41, AUD: 22.5, NZD: 21, CHF: 39, CAD: 25, AED: 9.4,
};

export const COUNTRY_TO_CURRENCY: Record<string, string> = {
  // Thai names (already normalized: lowercase/trim)
  "ไทย": "THB", "ประเทศไทย": "THB",
  "ญี่ปุ่น": "JPY",
  "เกาหลี": "KRW", "เกาหลีใต้": "KRW",
  "อเมริกา": "USD", "สหรัฐ": "USD", "สหรัฐอเมริกา": "USD",
  "อังกฤษ": "GBP", "สหราชอาณาจักร": "GBP",
  "จีน": "CNY", "ฮ่องกง": "HKD", "ไต้หวัน": "TWD",
  "สิงคโปร์": "SGD", "มาเลเซีย": "MYR", "เวียดนาม": "VND",
  "ลาว": "LAK", "กัมพูชา": "KHR", "พม่า": "MMK", "เมียนมา": "MMK",
  "อินโดนีเซีย": "IDR", "ฟิลิปปินส์": "PHP", "อินเดีย": "INR",
  "ฝรั่งเศส": "EUR", "เยอรมนี": "EUR", "อิตาลี": "EUR", "สเปน": "EUR",
  "ออสเตรเลีย": "AUD", "นิวซีแลนด์": "NZD", "สวิตเซอร์แลนด์": "CHF",
  "แคนาดา": "CAD", "ดูไบ": "AED", "สหรัฐอาหรับเอมิเรตส์": "AED",
  // English names + aliases (normalized lowercase)
  "thailand": "THB", "japan": "JPY",
  "korea": "KRW", "south korea": "KRW",
  "usa": "USD", "us": "USD", "united states": "USD", "america": "USD",
  "uk": "GBP", "united kingdom": "GBP", "england": "GBP", "britain": "GBP",
  "china": "CNY", "hong kong": "HKD", "taiwan": "TWD",
  "singapore": "SGD", "malaysia": "MYR", "vietnam": "VND",
  "laos": "LAK", "cambodia": "KHR", "myanmar": "MMK", "burma": "MMK",
  "indonesia": "IDR", "philippines": "PHP", "india": "INR",
  "france": "EUR", "germany": "EUR", "italy": "EUR", "spain": "EUR",
  "australia": "AUD", "new zealand": "NZD", "switzerland": "CHF",
  "canada": "CAD", "dubai": "AED", "uae": "AED",
  // ... extend with remaining countries from the ISO 3166-1 ↔ 4217 table
};
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test`
Expected: PASS (all `currency-by-country` tests green).

- [ ] **Step 5: Typecheck**

Run: `npm run typecheck`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/currency-by-country.ts test/currency-by-country.test.ts
git commit -m "feat: add country->currency data module"
```

---

## Task 3: Supabase `fx_rates` cache table

**Files:**
- Create: `db/2026-06-22-fx_rates.sql`

- [ ] **Step 1: Write the migration SQL**

Create `db/2026-06-22-fx_rates.sql`:

```sql
create table if not exists fx_rates (
  currency   text primary key,
  rate_thb   numeric not null,
  updated_at timestamptz not null default now()
);
```

- [ ] **Step 2: Apply it to Supabase**

Run the SQL in the Supabase SQL editor (or via the Supabase MCP `apply_migration`). Verify the table exists:

Expected: `select * from fx_rates;` returns 0 rows, no error.

- [ ] **Step 3: Commit**

```bash
git add db/2026-06-22-fx_rates.sql
git commit -m "feat: add fx_rates cache table migration"
```

---

## Task 4: `resolveBaseCurrency` in worker.ts

**Files:**
- Modify: `src/worker.ts` (add import at top; add function near `normalizeCurrency` ~`:766`; add `export` so it is testable)
- Test: `test/worker-currency.test.ts`

- [ ] **Step 1: Write failing test**

Create `test/worker-currency.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { resolveBaseCurrency } from "../src/worker";

describe("resolveBaseCurrency", () => {
  it("accepts a valid ISO currency code", () => {
    expect(resolveBaseCurrency("JPY")).toBe("JPY");
    expect(resolveBaseCurrency("eur")).toBe("EUR");
  });
  it("resolves Thai and English country names", () => {
    expect(resolveBaseCurrency("ญี่ปุ่น")).toBe("JPY");
    expect(resolveBaseCurrency("Japan")).toBe("JPY");
    expect(resolveBaseCurrency("ประเทศเกาหลีใต้")).toBe("KRW");
    expect(resolveBaseCurrency("usa")).toBe("USD");
  });
  it("fixes a typo'd currency code", () => {
    expect(resolveBaseCurrency("JYP")).toBe("JPY");
  });
  it("returns null for unknown input", () => {
    expect(resolveBaseCurrency("zzzz")).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run test/worker-currency.test.ts`
Expected: FAIL — `resolveBaseCurrency` is not exported / not defined.

- [ ] **Step 3: Add the import and function**

At the very top of `src/worker.ts`, add:

```ts
import {
  COUNTRY_TO_CURRENCY,
  ISO_4217,
  FALLBACK_RATES,
  normalizeCountryName,
  normalizeCurrencyCode,
} from "./currency-by-country";
```

Add this function near `normalizeCurrency` (around `:766`):

```ts
export function resolveBaseCurrency(input: string): string | null {
  const code = normalizeCurrencyCode(input);
  if (ISO_4217.has(code)) return code;
  const byCountry = COUNTRY_TO_CURRENCY[normalizeCountryName(input)];
  return byCountry || null;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run test/worker-currency.test.ts`
Expected: PASS.

- [ ] **Step 5: Typecheck**

Run: `npm run typecheck`
Expected: no errors. (Note: importing `FALLBACK_RATES` now even though it is used in Task 5 — if the typecheck flags an unused import, leave it; it is consumed in the next task.)

- [ ] **Step 6: Commit**

```bash
git add src/worker.ts test/worker-currency.test.ts
git commit -m "feat: resolveBaseCurrency (country/code -> currency)"
```

---

## Task 5: `getRateThb` + `getRatesForCurrencies` in worker.ts

**Files:**
- Modify: `src/worker.ts` (add both functions near `computeAmountThb` ~`:775`; add `export`)
- Test: `test/worker-currency.test.ts` (append)

- [ ] **Step 1: Write failing tests (append to test/worker-currency.test.ts)**

```ts
import { getRateThb } from "../src/worker";

function fakeEnv() {
  return {
    SUPABASE_URL: "https://example.supabase.co",
    SUPABASE_KEY: "test-key",
  } as any;
}

describe("getRateThb", () => {
  const realFetch = globalThis.fetch;
  afterEach(() => { globalThis.fetch = realFetch; });

  it("returns 1 for THB without any fetch", async () => {
    globalThis.fetch = (async () => { throw new Error("should not fetch"); }) as any;
    expect(await getRateThb(fakeEnv(), "THB")).toBe(1);
  });

  it("uses fresh cache when present", async () => {
    globalThis.fetch = (async (url: string) => {
      if (String(url).includes("fx_rates")) {
        return new Response(JSON.stringify([
          { currency: "JPY", rate_thb: 0.2, updated_at: new Date().toISOString() },
        ]), { status: 200 });
      }
      throw new Error("should not hit er-api when cache fresh");
    }) as any;
    expect(await getRateThb(fakeEnv(), "JPY")).toBe(0.2);
  });

  it("fetches live and upserts when cache missing", async () => {
    let upserted = false;
    globalThis.fetch = (async (url: string, init?: any) => {
      const u = String(url);
      if (u.includes("fx_rates") && (!init || init.method === undefined || init.method === "GET")) {
        return new Response(JSON.stringify([]), { status: 200 });
      }
      if (u.includes("fx_rates")) { upserted = true; return new Response("[]", { status: 200 }); }
      if (u.includes("open.er-api.com")) {
        return new Response(JSON.stringify({ result: "success", rates: { THB: 0.21 } }), { status: 200 });
      }
      throw new Error("unexpected url " + u);
    }) as any;
    const rate = await getRateThb(fakeEnv(), "JPY");
    expect(rate).toBe(0.21);
    expect(upserted).toBe(true);
  });

  it("falls back to FALLBACK_RATES when live fetch fails and no cache", async () => {
    globalThis.fetch = (async (url: string) => {
      const u = String(url);
      if (u.includes("fx_rates")) return new Response(JSON.stringify([]), { status: 200 });
      return new Response("err", { status: 500 });
    }) as any;
    // USD fallback is 34.5 in FALLBACK_RATES
    expect(await getRateThb(fakeEnv(), "USD")).toBe(34.5);
  });
});
```

Add `afterEach` to the import line: `import { describe, it, expect, afterEach } from "vitest";`

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run test/worker-currency.test.ts`
Expected: FAIL — `getRateThb` not exported.

- [ ] **Step 3: Implement the functions**

Add near `computeAmountThb` (~`:775`) in `src/worker.ts`:

```ts
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

  const rows = await supabaseSelect<any>(env, "fx_rates", "*", [`currency=eq.${curr}`]);
  const cached = rows?.[0];
  if (cached && Date.now() - Date.parse(cached.updated_at) < FX_CACHE_TTL_MS) {
    return Number(cached.rate_thb);
  }

  const live = await fetchLiveRateThb(curr);
  if (live) {
    await supabaseUpsert(env, "fx_rates",
      { currency: curr, rate_thb: live, updated_at: new Date().toISOString() }, "currency");
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run test/worker-currency.test.ts`
Expected: PASS.

- [ ] **Step 5: Typecheck**

Run: `npm run typecheck`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/worker.ts test/worker-currency.test.ts
git commit -m "feat: getRateThb with Supabase cache + live fetch + fallback"
```

---

## Task 6: Rewire trip creation flow

**Files:**
- Modify: `src/worker.ts` — prompts at `:133` and `:173`; `handleTripCurrency` at `:176`; `getTripBaseCurrency` at `:771`; `parseExpense` default at `:734`.

No new unit test (these touch DB + LINE; verified by typecheck + manual). The pure changes are covered by Task 4 tests.

- [ ] **Step 1: Update both prompts**

Replace the two identical prompt strings (`:133` and `:173`):

Old:
```ts
return reply(env, replyToken, "ระบุสกุลเงินหลักของทริป เช่น THB / JPY / USD / KRW");
```
New (both places):
```ts
return reply(env, replyToken, "ระบุประเทศของทริป เช่น ญี่ปุ่น / เกาหลี หรือพิมพ์รหัสสกุลเงิน เช่น JPY");
```

- [ ] **Step 2: Update `handleTripCurrency` (`:176`)**

Old (`:177-178`):
```ts
  const currency = normalizeCurrency(text);
  if (!currency) return reply(env, replyToken, "ไม่รู้จักสกุลเงินนี้ ตัวอย่าง: THB / JPY / USD / KRW");
```
New:
```ts
  const currency = resolveBaseCurrency(text);
  if (!currency) return reply(env, replyToken, "ไม่รู้จักประเทศ/สกุลเงินนี้ ลองพิมพ์ชื่อประเทศ เช่น ญี่ปุ่น หรือรหัสสกุล เช่น JPY");
```

- [ ] **Step 3: Update `getTripBaseCurrency` (`:771`)**

Old:
```ts
function getTripBaseCurrency(trip: Trip | null): string {
  return normalizeCurrency(trip?.base_currency || trip?.currency_code) || "THB";
}
```
New:
```ts
function getTripBaseCurrency(trip: Trip | null): string {
  const raw = normalizeCurrencyCode(trip?.base_currency || trip?.currency_code);
  return ISO_4217.has(raw) ? raw : "THB";
}
```

- [ ] **Step 4: Update `parseExpense` default (`:734-735`)**

Old:
```ts
  let currency = normalizeCurrency(parts[cursor] || "") || normalizeCurrency(defaultCurrency) || "THB";
  if (normalizeCurrency(parts[cursor] || "")) cursor++;
```
New (inline token stays strict 4-currency via `normalizeCurrency`; default trusts the already-valid trip currency):
```ts
  const inlineCurrency = normalizeCurrency(parts[cursor] || "");
  const fallbackCurrency = ISO_4217.has(normalizeCurrencyCode(defaultCurrency))
    ? normalizeCurrencyCode(defaultCurrency)
    : "THB";
  let currency = inlineCurrency || fallbackCurrency;
  if (inlineCurrency) cursor++;
```

- [ ] **Step 5: Typecheck**

Run: `npm run typecheck`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/worker.ts
git commit -m "feat: trip creation accepts country name or any ISO currency"
```

---

## Task 7: Live-THB summaries

**Files:**
- Modify: `src/worker.ts` — `buildTodayMessage` (`:829`), `buildTripTotalMessage` (`:814`), `buildEndTripSummary` (`:852`).

These read expenses, look up live rates via `getRatesForCurrencies`, and convert with the original `amount` (not stored `amount_thb`). Verified by typecheck + manual.

- [ ] **Step 1: Add a small helper for per-currency + THB aggregation**

Add near the summary builders:

```ts
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
```

- [ ] **Step 2: Rewrite `buildTripTotalMessage` (`:814`)**

```ts
async function buildTripTotalMessage(env: Env, userId: string, groupId: string | null): Promise<string> {
  const trip = await getActiveTrip(env, userId, groupId);
  if (!trip) return "ไม่มีทริปที่กำลังทำงานอยู่";
  const expenses = await getAllExpenses(env, trip.id);
  if (!expenses.length) return "ยังไม่มีรายการค่าใช้จ่าย";
  const rates = await getRatesForCurrencies(env, expenses.map((e) => e.currency || "THB"));
  const { lines, grandThb } = summarizeByCurrency(expenses, rates);
  const categories: Record<string, { total: number; people: Set<string> }> = {};
  for (const exp of expenses) addCategory(categories, exp, expenseThbLive(exp, rates));
  return `ยอดรวมทริป: ${trip.title}\n${lines.join("\n")}\nรวม ${Math.round(grandThb).toLocaleString()} บาท\n\n${formatCategorySummary(categories)}`;
}
```

- [ ] **Step 3: Rewrite `buildTodayMessage` (`:829`)**

```ts
async function buildTodayMessage(env: Env, userId: string, groupId: string | null): Promise<string> {
  const trip = await getActiveTrip(env, userId, groupId);
  if (!trip) return "ไม่มีทริปที่กำลังทำงานอยู่";
  const today = thaiDateString(new Date());
  const expenses = (await getAllExpenses(env, trip.id)).filter((e) => thaiDateFromIso(e.created_at || "") === today);
  if (!expenses.length) return `วันนี้ (${today}) ยังไม่มีรายจ่าย`;
  const rates = await getRatesForCurrencies(env, expenses.map((e) => e.currency || "THB"));
  const { lines, grandThb } = summarizeByCurrency(expenses, rates);
  const categories: Record<string, { total: number; people: Set<string> }> = {};
  for (const e of expenses) addCategory(categories, e, expenseThbLive(e, rates));
  return `ยอดวันนี้ (${today})\n${lines.join("\n")}\nรวมวันนี้: ${Math.round(grandThb).toLocaleString()} บาท\n\n${formatCategorySummary(categories)}`;
}
```

- [ ] **Step 4: Rewrite `buildEndTripSummary` totals to use live THB (`:852`)**

In `buildEndTripSummary`, before the expense loop add the rate map:

```ts
  const rates = await getRatesForCurrencies(env, expenses.map((e) => e.currency || "THB"));
```
Then replace `const amount = getExpenseAmountThb(exp);` (inside the loop, `:859`) with:
```ts
    const amount = expenseThbLive(exp, rates);
```
Leave the rest of the per-person split logic unchanged.

- [ ] **Step 5: Typecheck**

Run: `npm run typecheck`
Expected: no errors.

- [ ] **Step 6: Run full test suite**

Run: `npm test`
Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/worker.ts
git commit -m "feat: live THB conversion in today/total/end-trip summaries"
```

---

## Task 8: Deploy + manual verification

**Files:** none (deploy + manual LINE testing)

- [ ] **Step 1: Deploy the worker**

Run: `npm run deploy`
Expected: wrangler deploy succeeds.

- [ ] **Step 2: Manual — create a country trip**

In LINE: `ทริป ทดสอบยุโรป` → bot asks for country → reply `ฝรั่งเศส`.
Expected: "เริ่มทริปใหม่ ... สกุลเงินหลัก: EUR".

- [ ] **Step 3: Manual — add expenses and check summaries**

Add: `ข้าว 20 บอล` (defaults to EUR), then `กาแฟ 5 USD บอล` (inline USD still works for the 4).
Run `ยอดวันนี้`.
Expected: per-currency lines like `20 EUR | <thb> บาท` and `5 USD | <thb> บาท`, plus `รวมวันนี้` in THB; THB amounts reflect live rates (EUR ≈ 37–38, USD ≈ 32–35).

- [ ] **Step 4: Manual — nickname collision guard**

Add: `ข้าว 100 ต๊อป เบียร์`.
Expected: "ต๊อป" and "เบียร์" are both participants (not parsed as currency); amount defaults to trip currency.

- [ ] **Step 5: Manual — end trip split**

Run `จบทริป`.
Expected: per-person split totals in THB consistent with the live-converted today/total figures.

- [ ] **Step 6: Push to main (already authorized this session)**

```bash
git push origin main
```

---

## Self-Review

**Spec coverage:**
- Country/code input → Task 2 (data), Task 4 (`resolveBaseCurrency`), Task 6 (wiring). ✓
- All countries → Task 2 data module (with instruction to extend to full ISO list). ✓
- Live THB in ยอดวันนี้/ยอดรวม/จบทริป → Task 7. ✓
- `|`-separated per-currency display → Task 7 `summarizeByCurrency`. ✓
- Supabase `fx_rates` cache ≤12h + live fetch + file fallback → Task 3 + Task 5. ✓
- Not touching slip/export/showtime/computeAmountThb/daily-cron → respected (no tasks modify them). ✓
- Nickname-collision guard (inline token stays strict 4) → Task 6 Step 4 + Task 8 Step 4. ✓

**Placeholder scan:** Logic steps contain full code. The only "extend" instructions are for DATA (ISO_4217 / COUNTRY_TO_CURRENCY transcription from the canonical ISO table), with exact format and a working seed — acceptable data-entry, not logic placeholders.

**Type consistency:** `normalizeCurrencyCode`, `normalizeCountryName`, `COUNTRY_TO_CURRENCY`, `ISO_4217`, `FALLBACK_RATES` defined in Task 2 and imported/used consistently in Tasks 4–7. `getRateThb(env, currency)`, `getRatesForCurrencies(env, string[])`, `summarizeByCurrency(expenses, rates)`, `expenseThbLive(exp, rates)` signatures match across tasks. ✓
