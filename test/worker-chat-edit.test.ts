import { createHmac } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import worker from "../src/worker";

// Exercise the signed webhook; replace only LINE and database HTTP boundaries.
const expense = {
  id: 206, trip_id: "trip-1", payer_name: "บอล", item_name: "ค่าที่พัก",
  tag: "#ค่าที่พัก", amount: 4900, currency: "THB", amount_thb: 4900,
  exchange_rate_used: 1, exchange_rate_source: "identity",
  participants: ["บอล", "มิน"], created_at: "2026-09-01T00:00:00Z",
};

function setup(options: { active?: boolean; otherTrip?: boolean; state?: string; failPatch?: boolean } = {}) {
  const rows = [{ ...expense, participants: [...expense.participants], trip_id: options.otherTrip ? "trip-other" : "trip-1" }];
  const replies: Array<{ messages: Array<Record<string, unknown>> }> = [];
  const writes: Array<{ method: string; table: string; data: Record<string, unknown> }> = [];
  const trip = { id: "trip-1", status: "active", line_group_id: "group-1", creator_id: "user-1", base_currency: "THB" };
  const matches = (row: Record<string, unknown>, url: URL) => [...url.searchParams].every(([key, value]) =>
    !value.startsWith("eq.") || String(row[key]) === value.slice(3));
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(String(input));
    const method = init?.method || "GET";
    const data = init?.body ? JSON.parse(String(init.body)) : undefined;
    if (url.host === "api.line.me") {
      if (url.pathname === "/v2/bot/message/reply") {
        replies.push(data);
        return Response.json({});
      }
      if (method === "GET") return Response.json({ displayName: "บอล" });
    }
    if (url.host === "db.example") {
      const table = url.pathname.split("/").pop()!;
      if (method !== "GET") {
        writes.push({ method, table, data });
        if (table === "expenses" && method === "PATCH") {
          if (options.failPatch) return new Response("unavailable", { status: 503 });
          rows.filter(row => matches(row, url)).forEach(row => Object.assign(row, data));
        }
        return Response.json([]);
      }
      if (table === "bot_states") return Response.json(options.state ? [{ user_id: "user-1", group_id: "group-1", action: options.state, payload: {} }] : []);
      if (table === "trips") return Response.json(options.active === false ? [] : [trip].filter(row => matches(row, url)));
      if (table === "expenses") return Response.json(rows.filter(row => matches(row, url)));
    }
    throw new Error(`Unexpected HTTP request: ${method} ${url}`);
  }));

  async function send(text: string, groupId: string | null = "group-1") {
    const body = JSON.stringify({ events: [{ type: "message", replyToken: "reply-1", source: {
      type: groupId ? "group" : "user", userId: "user-1", ...(groupId ? { groupId } : {}),
    }, message: { type: "text", text } }] });
    const response = await worker.fetch(new Request("https://bot.example/callback", {
      method: "POST", body, headers: { "X-Line-Signature": createHmac("sha256", "test-secret").update(body).digest("base64") },
    }), {
      LINE_CHANNEL_SECRET: "test-secret", LINE_CHANNEL_ACCESS_TOKEN: "test-token",
      SUPABASE_URL: "https://db.example", SUPABASE_SERVICE_ROLE_KEY: "test-key",
    }, { waitUntil: vi.fn() } as unknown as ExecutionContext);
    expect(response.status).toBe(200);
    return JSON.stringify(replies);
  }
  return { rows, replies, writes, send };
}

afterEach(() => vi.unstubAllGlobals());

describe("ordinary chat", () => {
  it.each([true, false])("does not reply to conversation (active trip: %s)", async active => {
    const bot = setup({ active });
    await bot.send("น้องจอมกับใหญ่อยู่นะ");
    expect(bot.replies).toEqual([]);
    expect(bot.writes).toEqual([]);
  });

  it("does not reply to unrelated direct messages", async () => {
    const bot = setup();
    await bot.send("ไว้เจอกันนะ", null);
    expect(bot.replies).toEqual([]);
  });

  it("continues answering an explicit help command", async () => {
    expect(await setup().send("help")).toContain("คำสั่งทั้งหมด");
  });

  it("continues accepting a requested trip name", async () => {
    const bot = setup({ state: "wait_trip_name" });
    expect(await bot.send("เที่ยวกับเพื่อน")).toContain("ระบุประเทศ");
    expect(bot.writes).toContainEqual(expect.objectContaining({ table: "bot_states", data: expect.objectContaining({ action: "wait_trip_currency", payload: { trip_name: "เที่ยวกับเพื่อน" } }) }));
  });
});

describe("expense name editing", () => {
  it("shows the rename syntax and example on the edit card", async () => {
    const output = await setup().send("edit");
    expect(output).toContain("edit [ID] name [ชื่อใหม่]");
    expect(output).toContain("edit 0206 name");
    expect(output).toContain("edit [ID] [ยอดใหม่]");
  });

  it.each(["edit 0206 name ค่าที่พัก คืนแรก", "EDIT 0206 NAME #ค่าที่พัก คืนแรก"])("renames labels without changing money or people: %s", async command => {
    const bot = setup();
    const output = await bot.send(command);
    expect(bot.rows[0]).toEqual({ ...expense, item_name: "ค่าที่พัก คืนแรก", tag: "#ค่าที่พัก คืนแรก" });
    expect(bot.writes).toEqual([{ method: "PATCH", table: "expenses", data: { item_name: "ค่าที่พัก คืนแรก", tag: "#ค่าที่พัก คืนแรก" } }]);
    expect(output).toContain("ค่าที่พัก คืนแรก");
    expect(output).toContain("0206");
    expect(await bot.send("edit")).toContain("#ค่าที่พัก คืนแรก");
  });

  it.each(["edit 0206 name", "edit 0206 name #", "edit 0206 name    "])("rejects an empty name: %s", async command => {
    const bot = setup();
    expect(await bot.send(command)).toContain("กรุณาระบุชื่อใหม่");
    expect(bot.writes).toEqual([]);
  });

  it("does not rename an expense from a different trip", async () => {
    const bot = setup({ otherTrip: true });
    expect(await bot.send("edit 0206 name ใหม่")).toContain("ไม่พบรายการนี้");
    expect(bot.writes).toEqual([]);
  });

  it("requires an active trip", async () => {
    const bot = setup({ active: false });
    expect(await bot.send("edit 0206 name ใหม่")).toContain("ไม่มีทริป");
    expect(bot.writes).toEqual([]);
  });

  it("does not fall back to a personal trip when another group has no trip", async () => {
    const bot = setup();
    expect(await bot.send("edit 0206 name ใหม่", "group-other")).toContain("ไม่มีทริป");
    expect(bot.writes).toEqual([]);
  });

  it("allows renaming in the creator's direct conversation", async () => {
    const bot = setup();
    await bot.send("edit 0206 name ใหม่", null);
    expect(bot.rows[0].tag).toBe("#ใหม่");
  });

  it("reports a failed save without confirming a successful rename", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const bot = setup({ failPatch: true });
    const output = await bot.send("edit 0206 name ใหม่");
    expect(output).toContain("เกิด error");
    expect(output).not.toContain("แก้ชื่อ ID");
    expect(bot.rows[0]).toEqual(expense);
    vi.restoreAllMocks();
  });

  it("preserves the existing amount edit command", async () => {
    const bot = setup();
    expect(await bot.send("edit 0206 88")).toContain("88 THB");
    expect(bot.rows[0]).toMatchObject({ amount: 88, amount_thb: 88, item_name: "ค่าที่พัก", tag: "#ค่าที่พัก" });
  });
});
