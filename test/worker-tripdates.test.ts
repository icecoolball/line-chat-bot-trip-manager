import { describe, it, expect } from "vitest";
import { parseTripDates } from "../src/worker";

describe("parseTripDates", () => {
  it("parses a DD/MM/YYYY range", () => {
    expect(parseTripDates("23/06/2026-27/06/2026")).toEqual({ start: "2026-06-23", end: "2026-06-27" });
  });

  it("parses a range with spaces and ถึง", () => {
    expect(parseTripDates("23/06/2026 ถึง 27/06/2026")).toEqual({ start: "2026-06-23", end: "2026-06-27" });
  });

  it("parses a single start date (no end)", () => {
    expect(parseTripDates("23/06/2026")).toEqual({ start: "2026-06-23", end: null });
  });

  it("parses ISO YYYY-MM-DD", () => {
    expect(parseTripDates("2026-06-23")).toEqual({ start: "2026-06-23", end: null });
  });

  it("converts Buddhist year to CE", () => {
    expect(parseTripDates("23/06/2569")).toEqual({ start: "2026-06-23", end: null });
  });

  it("treats ข้าม / skip as no dates", () => {
    expect(parseTripDates("ข้าม")).toEqual({ start: null, end: null });
    expect(parseTripDates("-")).toEqual({ start: null, end: null });
  });

  it("returns null for unparseable text", () => {
    expect(parseTripDates("ไม่มีวันที่เลย")).toBeNull();
    expect(parseTripDates("")).toBeNull();
  });

  it("ignores invalid month/day", () => {
    // 45/13/2026 -> invalid, no other date -> null
    expect(parseTripDates("45/13/2026")).toBeNull();
  });
});
