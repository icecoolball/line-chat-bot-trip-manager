import { describe, it, expect } from "vitest";
import { parseExpense, parseSlipAssignment } from "../src/worker";

describe("parseExpense (canonical: ผู้จ่าย #หมวด ยอด คนหาร...)", () => {
  it("parses payer, tag, amount, participants", () => {
    const r = parseExpense("บอล #ค่าข้าว 120 บอล ปาค มิน", "sender", "THB");
    expect(r).toMatchObject({
      payer: "บอล",
      tag: "#ค่าข้าว",
      item: "ค่าข้าว",
      amount: 120,
      currency: "THB",
      participants: ["บอล", "ปาค", "มิน"],
    });
  });

  it("payer can pay without sharing (not in participant list)", () => {
    const r = parseExpense("บอล #ค่าข้าว 120 ปาค มิน", "sender", "THB");
    expect(r?.payer).toBe("บอล");
    expect(r?.participants).toEqual(["ปาค", "มิน"]);
  });

  it("defaults payer to sender when no name before amount", () => {
    const r = parseExpense("#ค่าข้าว 120 ปาค มิน", "sender", "THB");
    expect(r?.payer).toBe("sender");
    expect(r?.participants).toEqual(["ปาค", "มิน"]);
  });

  it("uses trip currency by default; accepts strict inline currency", () => {
    expect(parseExpense("บอล #ค่าข้าว 120 ปาค", "s", "EUR")?.currency).toBe("EUR");
    expect(parseExpense("บอล #ค่าข้าว 120 JPY ปาค", "s", "EUR")?.currency).toBe("JPY");
  });

  it("returns null when no amount or no participants", () => {
    expect(parseExpense("บอล #ค่าข้าว ปาค", "s")).toBeNull();
    expect(parseExpense("บอล #ค่าข้าว 120", "s")).toBeNull();
  });
});

describe("parseSlipAssignment (ผู้จ่าย #หมวด คนหาร...)", () => {
  it("first name is payer, rest are participants", () => {
    const r = parseSlipAssignment("บอล #ค่าข้าว บอล ปาค มิน");
    expect(r.payer).toBe("บอล");
    expect(r.participants).toEqual(["บอล", "ปาค", "มิน"]);
    expect(r.tag).toBe("#ค่าข้าว");
  });

  it("payer not sharing", () => {
    const r = parseSlipAssignment("บอล #ค่าข้าว ปาค มิน");
    expect(r.payer).toBe("บอล");
    expect(r.participants).toEqual(["ปาค", "มิน"]);
  });
});
