import { describe, it, expect } from "vitest";
import { computeSettlement } from "../src/worker";

function sum(transfers: Array<{ amount: number }>): number {
  return transfers.reduce((s, t) => s + t.amount, 0);
}

describe("computeSettlement", () => {
  it("returns no transfers when everyone paid exactly their share", () => {
    const paid = { a: 100, b: 100 };
    const owed = { a: 100, b: 100 };
    expect(computeSettlement(paid, owed)).toEqual([]);
  });

  it("routes all debtors to a single creditor", () => {
    // ปาค จ่าย 15072.87, ต้องจ่าย 5388.55 -> เจ้าหนี้คนเดียว
    const paid = { บอล: 4861, ปาค: 15072.87 };
    const owed = { บอล: 5388.55, ปาค: 5388.55, พี่เล็ก: 1620.33, เอ้: 3768.22, มิน: 3768.22 };
    const t = computeSettlement(paid, owed);
    // ทุกคนที่ติดลบโอนเข้าปาคคนเดียว
    expect(t.every((x) => x.to === "ปาค")).toBe(true);
    expect(t.map((x) => x.from).sort()).toEqual(["บอล", "พี่เล็ก", "มิน", "เอ้"].sort());
    // ยอดรวมที่โอน = ยอดที่ปาคควรได้คืน (9684.32) ± epsilon
    expect(sum(t)).toBeCloseTo(9684.32, 1);
  });

  it("matches multiple creditors and debtors, total transfers balance", () => {
    const paid = { a: 300, b: 0, c: 0, d: 90 };
    const owed = { a: 0, b: 150, c: 150, d: 90 };
    const t = computeSettlement(paid, owed);
    // a เป็นเจ้าหนี้ 300, b และ c เป็นลูกหนี้คนละ 150, d สมดุล
    expect(sum(t)).toBeCloseTo(300, 5);
    expect(t.every((x) => x.to === "a")).toBe(true);
    expect(t.find((x) => x.from === "d")).toBeUndefined();
  });

  it("ignores sub-cent imbalances", () => {
    const paid = { a: 100.004, b: 99.996 };
    const owed = { a: 100, b: 100 };
    expect(computeSettlement(paid, owed)).toEqual([]);
  });
});
