const test = require("node:test");
const assert = require("node:assert/strict");
const {
  COOKIE_NAME,
  createSession,
  parseCookies,
  readSession,
  sessionCookie,
  validateInvite,
  verifySession,
} = require("../lib/auth");

test("invite comparison and signed session lifecycle", () => {
  const now = Date.UTC(2026, 5, 24);
  assert.equal(validateInvite("family-secret", "family-secret"), true);
  assert.equal(validateInvite("wrong", "family-secret"), false);

  const token = createSession("family-secret", "11111111-1111-4111-8111-111111111111", now);
  assert.equal(verifySession(token, "family-secret", now + 1000), true);
  assert.equal(verifySession(token, "rotated-secret", now + 1000), false);
  assert.equal(verifySession(token, "family-secret", now + 31 * 24 * 60 * 60 * 1000), false);
  assert.equal(readSession(token, "family-secret", now + 1000).memberId, "11111111-1111-4111-8111-111111111111");
});

test("rotating the family invite secret invalidates existing sessions", () => {
  const now = Date.UTC(2026, 5, 24);
  const token = createSession("family-secret", "11111111-1111-4111-8111-111111111111", now);
  assert.equal(verifySession(token, "family-secret", now + 1000), true);
  assert.equal(verifySession(token, "family-secret-rotated", now + 1000), false);
});

test("session cookie is HttpOnly and parsed safely", () => {
  const cookie = sessionCookie("abc.def", true);
  assert.match(cookie, new RegExp(`^${COOKIE_NAME}=`));
  assert.match(cookie, /HttpOnly/);
  assert.match(cookie, /SameSite=Strict/);
  assert.match(cookie, /Secure/);
  assert.equal(parseCookies("other=1; ticket_family_session=abc.def")[COOKIE_NAME], "abc.def");
});
