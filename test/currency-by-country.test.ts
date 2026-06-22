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
