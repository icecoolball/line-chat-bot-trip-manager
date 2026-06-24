const test = require("node:test");
const assert = require("node:assert/strict");
const { createScheduleStore, mapSchedule } = require("../lib/schedule-store");

test("maps database schedule and reminder state to the public API shape", () => {
  assert.deepEqual(mapSchedule({
    id: "event-1",
    name: "Family concert",
    site: "Eventpop",
    url: "https://example.com/event",
    sale_at: "2026-07-01T03:00:00.000Z",
    status: "active",
    created_at: "2026-06-24T00:00:00.000Z",
    schedule_reminders: [{
      offset_minutes: 5,
      due_at: "2026-07-01T02:55:00.000Z",
      status: "pending",
      sent_at: null,
      attempt_count: 0,
      last_error: null,
    }],
  }), {
    id: "event-1",
    name: "Family concert",
    site: "Eventpop",
    url: "https://example.com/event",
    saleAt: "2026-07-01T03:00:00.000Z",
    status: "active",
    createdAt: "2026-06-24T00:00:00.000Z",
    reminders: [{
      offsetMinutes: 5,
      dueAt: "2026-07-01T02:55:00.000Z",
      status: "pending",
      sentAt: null,
      attemptCount: 0,
      lastError: null,
    }],
  });
});

test("uses only scoped RPCs with the backend token", async () => {
  const calls = [];
  const row = {
    id: "event-1",
    name: "Family concert",
    site: "Eventpop",
    url: "https://example.com/event",
    sale_at: "2026-07-01T03:00:00.000Z",
    status: "active",
    created_at: "2026-06-24T00:00:00.000Z",
    schedule_reminders: [],
  };
  const supabase = {
    async rpc(name, parameters) {
      calls.push({ name, parameters });
      if (name === "ticket_list_schedules") return { data: [row], error: null };
      if (name === "create_ticket_schedule") return { data: row, error: null };
      return { data: true, error: null };
    },
  };
  const store = createScheduleStore(supabase, "backend-secret");

  await store.list();
  await store.create({
    name: row.name,
    site: row.site,
    url: row.url,
    saleAt: row.sale_at,
  });
  await store.remove("00000000-0000-4000-8000-000000000000");

  assert.deepEqual(calls.map((call) => call.name), [
    "ticket_list_schedules",
    "create_ticket_schedule",
    "delete_ticket_schedule",
  ]);
  assert.equal(calls.every((call) => call.parameters.p_backend_token === "backend-secret"), true);
});
