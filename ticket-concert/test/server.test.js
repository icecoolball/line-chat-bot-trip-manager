const test = require("node:test");
const assert = require("node:assert/strict");
const http = require("http");
const { createRequestHandler, createRuntimeHandler } = require("../server");

async function withServer(run) {
  const schedules = [];
  const handler = createRequestHandler({
    familyAccessToken: "family-test-token",
    sourceInspector: async () => ({
      finalUrl: "https://example.com/event",
      sourceDate: "Wed, 24 Jun 2026 12:00:00 GMT",
      matchedText: "เปิดขาย 01/07/2026 10:00",
    }),
    scheduleStore: {
      async list() { return schedules; },
      async create(payload) {
        const schedule = { id: "11111111-1111-4111-8111-111111111111", ...payload, reminders: [] };
        schedules.push(schedule);
        return schedule;
      },
      async remove(id) {
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

test("protected APIs require invite exchange and signed cookie", async () => {
  await withServer(async (baseUrl) => {
    const unauthorized = await fetch(`${baseUrl}/api/schedules`);
    assert.equal(unauthorized.status, 401);

    const badInvite = await fetch(`${baseUrl}/api/session/invite`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ token: "wrong" }),
    });
    assert.equal(badInvite.status, 401);

    const invite = await fetch(`${baseUrl}/api/session/invite`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ token: "family-test-token" }),
    });
    assert.equal(invite.status, 200);
    const cookie = invite.headers.get("set-cookie").split(";")[0];

    const session = await fetch(`${baseUrl}/api/session`, { headers: { cookie } });
    assert.equal(session.status, 200);

    const source = await fetch(`${baseUrl}/api/source-inspect`, {
      method: "POST",
      headers: { cookie, "content-type": "application/json" },
      body: JSON.stringify({ url: "https://example.com/event" }),
    });
    assert.equal(source.status, 200);
    assert.match((await source.json()).matchedText, /01\/07\/2026/);
  });
});

test("creates and deletes a schedule without accepting a LINE target", async () => {
  await withServer(async (baseUrl) => {
    const invite = await fetch(`${baseUrl}/api/session/invite`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ token: "family-test-token" }),
    });
    const cookie = invite.headers.get("set-cookie").split(";")[0];
    const created = await fetch(`${baseUrl}/api/schedules`, {
      method: "POST",
      headers: { cookie, "content-type": "application/json" },
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

    const removed = await fetch(`${baseUrl}/api/schedules/${schedule.id}`, { method: "DELETE", headers: { cookie } });
    assert.equal(removed.status, 200);
    const list = await fetch(`${baseUrl}/api/schedules`, { headers: { cookie } });
    assert.deepEqual((await list.json()).schedules, []);
  });
});

test("runtime startup requires the backend token secret", () => {
  assert.throws(() => createRuntimeHandler({
    FAMILY_ACCESS_TOKEN: "family-test-token",
    SUPABASE_URL: "https://example.supabase.co",
    SUPABASE_ANON_KEY: "anon-key",
  }), /TICKET_BACKEND_TOKEN/);
});
