const crypto = require("crypto");
const { createBackendCredential } = require("./backend-credential");

function mapSchedule(row) {
  return {
    id: row.id,
    name: row.name,
    site: row.site,
    url: row.url,
    saleAt: row.sale_at,
    status: row.status,
    createdAt: row.created_at,
    reminders: (row.schedule_reminders || []).map((item) => ({
      offsetMinutes: item.offset_minutes,
      dueAt: item.due_at,
      status: item.status,
      sentAt: item.sent_at,
      attemptCount: item.attempt_count,
      lastError: item.last_error,
    })),
  };
}

function createScheduleStore(supabase, backendSecret) {
  return {
    async list(memberId) {
      const credential = createBackendCredential(backendSecret, memberId, "ticket:schedules:list");
      const { data, error } = await supabase.rpc("ticket_list_schedules", {
        p_backend_credential: credential,
      });
      if (error) throw error;
      return (data || []).map(mapSchedule);
    },

    async create(memberId, payload) {
      const id = crypto.randomUUID();
      const credential = createBackendCredential(backendSecret, memberId, "ticket:schedules:create");
      const { data, error } = await supabase.rpc("create_ticket_schedule", {
        p_backend_credential: credential,
        p_id: id,
        p_name: payload.name,
        p_site: payload.site,
        p_url: payload.url,
        p_sale_at: payload.saleAt,
      });
      if (error) throw error;
      return mapSchedule(Array.isArray(data) ? data[0] : data);
    },

    async remove(memberId, id) {
      const credential = createBackendCredential(backendSecret, memberId, "ticket:schedules:delete");
      const { error } = await supabase.rpc("delete_ticket_schedule", {
        p_backend_credential: credential,
        p_id: id,
      });
      if (error) throw error;
    },
  };
}

module.exports = { createScheduleStore, mapSchedule };
