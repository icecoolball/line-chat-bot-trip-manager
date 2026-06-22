import { describe, it, expect, afterEach } from "vitest";
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
