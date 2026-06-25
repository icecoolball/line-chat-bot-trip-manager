const test = require("node:test");
const assert = require("node:assert/strict");
const http = require("http");
const { createRequestHandler, createRuntimeHandler } = require("../server");

async function withServer(run) {
  const schedules = [];
  const members = [{ id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", name: "Alice", status: "active" }];
  const inviteTokens = new Map([["family-test-token", members[0].id]]);
  const handler = createRequestHandler({
    familyAccessToken: "family-test-token",
    memberStore: {
      async exchangeInvite(token) {
        const memberId = inviteTokens.get(token);
        return members.find((item) => item.id === memberId && item.status === "active") || null;
      },
      async bootstrapLegacyMember() {
        return members[0];
      },
      async getActiveMember(memberId) {
        return members.find((item) => item.id === memberId && item.status === "active") || null;
      },
      async list() {
        return members;
      },
      async create(_memberId, name, inviteToken) {
        const member = { id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", name, status: "active" };
        members.push(member);
        inviteTokens.set(inviteToken, member.id);
        return member;
      },
      async revoke(_memberId, targetMemberId) {
        const target = members.find((item) => item.id === targetMemberId);
        if (target) target.status = "revoked";
        return true;
      },
    },
    sourceInspector: async () => ({
      finalUrl: "https://example.com/event",
      sourceDate: "Wed, 24 Jun 2026 12:00:00 GMT",
      matchedText: "เปิดขาย 01/07/2026 10:00",
    }),
    scheduleStore: {
      async list() { return schedules; },
      async create(_memberId, payload) {
        const schedule = { id: "11111111-1111-4111-8111-111111111111", ...payload, reminders: [] };
        schedules.push(schedule);
        return schedule;
      },
      async remove(_memberId, id) {
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
    assert.equal((await session.json()).member.name, "Alice");

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

test("revoked member sessions stop working", async () => {
  await withServer(async (baseUrl) => {
    const aliceInvite = await fetch(`${baseUrl}/api/session/invite`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ token: "family-test-token" }),
    });
    const aliceCookie = aliceInvite.headers.get("set-cookie").split(";")[0];

    const createdMember = await fetch(`${baseUrl}/api/members`, {
      method: "POST",
      headers: { cookie: aliceCookie, "content-type": "application/json" },
      body: JSON.stringify({ name: "Bob" }),
    });
    assert.equal(createdMember.status, 201);
    const createdBody = await createdMember.json();
    const bobInviteToken = new URL(createdBody.inviteUrl).hash.slice("#invite=".length);

    const bobInvite = await fetch(`${baseUrl}/api/session/invite`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ token: bobInviteToken }),
    });
    assert.equal(bobInvite.status, 200);
    const bobCookie = bobInvite.headers.get("set-cookie").split(";")[0];

    const members = await fetch(`${baseUrl}/api/members`, { headers: { cookie: aliceCookie } });
    const bob = (await members.json()).members.find((item) => item.name === "Bob");
    assert.ok(bob);

    const revoke = await fetch(`${baseUrl}/api/members/${bob.id}`, { method: "DELETE", headers: { cookie: aliceCookie } });
    assert.equal(revoke.status, 200);

    const afterRevoke = await fetch(`${baseUrl}/api/session`, { headers: { cookie: bobCookie } });
    assert.equal(afterRevoke.status, 401);
  });
});

test("runtime startup requires the backend token secret", () => {
  assert.throws(() => createRuntimeHandler({
    FAMILY_ACCESS_TOKEN: "family-test-token",
    SUPABASE_URL: "https://example.supabase.co",
    SUPABASE_ANON_KEY: "anon-key",
  }), /TICKET_BACKEND_TOKEN/);
});
