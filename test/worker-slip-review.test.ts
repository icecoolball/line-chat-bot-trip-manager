import { describe, expect, it } from "vitest";
import { buildSlipBatchSavedMessage, getSlipReviewStep } from "../src/worker";

describe("getSlipReviewStep", () => {
  it("asks for confirmation one receipt at a time", () => {
    expect(getSlipReviewStep([133, 180], 0, "TWD")).toEqual({
      action: "wait_slip_confirm",
      amount: 133,
      message: "ใบที่ 1/2: ตรวจพบ 133 TWD\nถ้าถูกต้องพิมพ์: ใช่\nถ้าไม่ถูก พิมพ์: ไม่ [ยอดที่ถูก]\nเช่น ไม่ 180",
    });
    expect(getSlipReviewStep([133, 180], 1, "TWD")).toMatchObject({
      action: "wait_slip_confirm",
      amount: 180,
    });
  });

  it("asks for a manual amount only for the unreadable receipt", () => {
    expect(getSlipReviewStep([133, null], 1, "TWD")).toEqual({
      action: "wait_slip_amount",
      amount: null,
      message: "ใบที่ 2/2: อ่านยอดไม่ได้\nพิมพ์ยอดของใบนี้ เช่น 120 หรือ 120.50",
    });
  });
});

describe("buildSlipBatchSavedMessage", () => {
  it("lists each saved receipt as a separate expense", () => {
    expect(buildSlipBatchSavedMessage([133, 180], ["41", "42"], "TWD")).toBe(
      "บันทึกครบ 2 ใบ แยกเป็น 2 รายการ\nใบที่ 1: 133 TWD (ID 41)\nใบที่ 2: 180 TWD (ID 42)",
    );
  });
});
