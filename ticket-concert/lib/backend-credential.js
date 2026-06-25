const crypto = require("crypto");

const BACKEND_CREDENTIAL_TTL_SECONDS = 60;

function signBackendCredential(secret, memberId, scope, expiresAt) {
  return crypto
    .createHmac("sha256", secret)
    .update(`ticket-rpc:${memberId}:${scope}:${expiresAt}`)
    .digest("hex");
}

function createBackendCredential(secret, memberId, scope, nowMs = Date.now()) {
  if (!secret) throw new Error("TICKET_BACKEND_TOKEN is required");
  if (!memberId) throw new Error("memberId is required");
  if (!scope) throw new Error("scope is required");
  const expiresAt = Math.floor(nowMs / 1000) + BACKEND_CREDENTIAL_TTL_SECONDS;
  return `${memberId}.${scope}.${expiresAt}.${signBackendCredential(secret, memberId, scope, expiresAt)}`;
}

module.exports = {
  BACKEND_CREDENTIAL_TTL_SECONDS,
  createBackendCredential,
  signBackendCredential,
};
