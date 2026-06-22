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
