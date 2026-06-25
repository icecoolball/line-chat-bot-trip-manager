const { createBackendCredential } = require("./backend-credential");

function normalizeMember(row) {
  return {
    id: row.id,
    name: row.name,
    status: row.status,
    createdAt: row.created_at,
    revokedAt: row.revoked_at,
    lastUsedAt: row.last_used_at,
  };
}

function firstRow(data) {
  if (Array.isArray(data)) return data[0] || null;
  return data || null;
}

function createMemberStore(supabase, backendSecret) {
  return {
    async exchangeInvite(token) {
      const { data, error } = await supabase.rpc("ticket_exchange_invite", {
        p_invite_token: token,
      });
      if (error) throw error;
      const member = firstRow(data);
      return member ? normalizeMember(member) : null;
    },

    async bootstrapLegacyMember(token) {
      const { data, error } = await supabase.rpc("ticket_bootstrap_legacy_member", {
        p_bootstrap_token: token,
      });
      if (error) throw error;
      const member = firstRow(data);
      return member ? normalizeMember(member) : null;
    },

    async getActiveMember(memberId) {
      const credential = createBackendCredential(backendSecret, memberId, "ticket:session");
      const { data, error } = await supabase.rpc("ticket_get_member_session", {
        p_backend_credential: credential,
      });
      if (error) throw error;
      const member = firstRow(data);
      return member ? normalizeMember(member) : null;
    },

    async list(memberId) {
      const credential = createBackendCredential(backendSecret, memberId, "ticket:members:list");
      const { data, error } = await supabase.rpc("ticket_list_members", {
        p_backend_credential: credential,
      });
      if (error) throw error;
      return (data || []).map(normalizeMember);
    },

    async create(memberId, name, inviteToken) {
      const credential = createBackendCredential(backendSecret, memberId, "ticket:members:create");
      const { data, error } = await supabase.rpc("ticket_create_member", {
        p_backend_credential: credential,
        p_name: name,
        p_invite_token: inviteToken,
      });
      if (error) throw error;
      const member = firstRow(data);
      return member ? normalizeMember(member) : null;
    },

    async revoke(memberId, targetMemberId) {
      const credential = createBackendCredential(backendSecret, memberId, "ticket:members:revoke");
      const { data, error } = await supabase.rpc("ticket_revoke_member", {
        p_backend_credential: credential,
        p_member_id: targetMemberId,
      });
      if (error) throw error;
      return Boolean(data);
    },
  };
}

module.exports = { createMemberStore, normalizeMember };
