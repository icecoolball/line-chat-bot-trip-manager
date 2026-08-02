const test = require("node:test");
const assert = require("node:assert/strict");
const http = require("http");
const { createRequestHandler, createRuntimeHandler } = require("../server");

const PUBLIC_MEMBER_ID = "af771c3c-1046-4cf6-b98e-d8249b1dc68e";

async function withServer(run) {
  const schedules = [];
  const handler = createRequestHandler({
    publicMemberId: PUBLIC_MEMBER_ID,
    sourceInspector: async () => ({
      finalUrl: "https://example.com/event",
      sourceDate: "Wed, 24 Jun 2026 12:00:00 GMT",
      matchedText: "เปิดขาย 01/07/2026 10:00",
    }),
    scheduleStore: {
      async list(memberId) {
        assert.equal(memberId, PUBLIC_MEMBER_ID);
        return schedules;
      },
      async create(memberId, payload) {
        assert.equal(memberId, PUBLIC_MEMBER_ID);
        const schedule = { id: "11111111-1111-4111-8111-111111111111", ...payload, reminders: [] };
        schedules.push(schedule);
        return schedule;
      },
      async remove(memberId, id) {
        assert.equal(memberId, PUBLIC_MEMBER_ID);
        const index = schedules.findIndex((item) => item.id === id);
        if (index >= 0) schedules.splice(index, 1);
      },
    },
  });
  const server = http.createServer(handler);
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    await run(`http://127.0.0.1:${server.address().port}`);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
}

test("public APIs work without an invite or session cookie", async () => {
  await withServer(async (baseUrl) => {
    const schedules = await fetch(`${baseUrl}/api/schedules`);
    assert.equal(schedules.status, 200);

    const source = await fetch(`${baseUrl}/api/source-inspect`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ url: "https://example.com/event" }),
    });
    assert.equal(source.status, 200);
    assert.match((await source.json()).matchedText, /01\/07\/2026/);
  });
});

test("creates and deletes a schedule without accepting a LINE target", async () => {
  await withServer(async (baseUrl) => {
    const created = await fetch(`${baseUrl}/api/schedules`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        name: "Family concert",
        site: "Eventpop",
        url: "https://example.com/event",
        saleAt: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
        targetId: "must-not-be-used",
      }),
    });
    assert.equal(created.status, 201);
    const schedule = (await created.json()).schedule;
    assert.equal("targetId" in schedule, false);

    const removed = await fetch(`${baseUrl}/api/schedules/${schedule.id}`, { method: "DELETE" });
    assert.equal(removed.status, 200);
    const list = await fetch(`${baseUrl}/api/schedules`);
    assert.deepEqual((await list.json()).schedules, []);
  });
});

test("runtime startup requires the backend token secret", () => {
  assert.throws(() => createRuntimeHandler({
    SUPABASE_URL: "https://example.supabase.co",
    SUPABASE_ANON_KEY: "anon-key",
  }), /TICKET_BACKEND_TOKEN/);
});
