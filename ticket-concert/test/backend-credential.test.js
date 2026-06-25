const test = require("node:test");
const assert = require("node:assert/strict");
const {
  BACKEND_CREDENTIAL_TTL_SECONDS,
  createBackendCredential,
  signBackendCredential,
} = require("../lib/backend-credential");

test("creates a short-lived signed backend credential", () => {
  const now = Date.UTC(2026, 5, 25, 0, 0, 0);
  const credential = createBackendCredential("backend-secret", "11111111-1111-4111-8111-111111111111", "ticket:schedules:list", now);
  const [memberId, scope, expiresAt, signature] = credential.split(".");
  assert.equal(memberId, "11111111-1111-4111-8111-111111111111");
  assert.equal(scope, "ticket:schedules:list");
  assert.equal(Number(expiresAt), Math.floor(now / 1000) + BACKEND_CREDENTIAL_TTL_SECONDS);
  assert.equal(signature, signBackendCredential("backend-secret", memberId, scope, expiresAt));
});
