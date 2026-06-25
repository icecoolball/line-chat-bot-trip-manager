const test = require("node:test");
const assert = require("node:assert/strict");
const { createMemberStore } = require("../lib/member-store");

test("member store exchanges invites and issues scoped RPC credentials", async () => {
  const calls = [];
  const supabase = {
    async rpc(name, parameters) {
      calls.push({ name, parameters });
      if (name === "ticket_exchange_invite") {
        return { data: [{ id: "11111111-1111-4111-8111-111111111111", name: "Alice", status: "active" }], error: null };
      }
      if (name === "ticket_get_member_session") {
        return { data: [{ id: "11111111-1111-4111-8111-111111111111", name: "Alice", status: "active" }], error: null };
      }
      if (name === "ticket_list_members") {
        return { data: [{ id: "11111111-1111-4111-8111-111111111111", name: "Alice", status: "active" }], error: null };
      }
      if (name === "ticket_create_member") {
        return { data: [{ id: "22222222-2222-4222-8222-222222222222", name: "Bob", status: "active" }], error: null };
      }
      return { data: true, error: null };
    },
  };
  const store = createMemberStore(supabase, "backend-secret");

  const member = await store.exchangeInvite("invite-token");
  assert.equal(member.name, "Alice");
  await store.getActiveMember(member.id);
  await store.list(member.id);
  await store.create(member.id, "Bob", "bob-invite");
  await store.revoke(member.id, "22222222-2222-4222-8222-222222222222");

  assert.deepEqual(calls.map((call) => call.name), [
    "ticket_exchange_invite",
    "ticket_get_member_session",
    "ticket_list_members",
    "ticket_create_member",
    "ticket_revoke_member",
  ]);
  assert.equal(calls.slice(1).every((call) => typeof call.parameters.p_backend_credential === "string"), true);
});
