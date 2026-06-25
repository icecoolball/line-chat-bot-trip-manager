const crypto = require("crypto");

const COOKIE_NAME = "ticket_family_session";
const SESSION_TTL_SECONDS = 30 * 24 * 60 * 60;

function encode(value) {
  return Buffer.from(value).toString("base64url");
}

function sign(value, secret) {
  return crypto.createHmac("sha256", secret).update(`ticket-family:${value}`).digest("base64url");
}

function safeEqual(left, right) {
  const a = Buffer.from(String(left));
  const b = Buffer.from(String(right));
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

function validateInvite(candidate, secret) {
  return Boolean(secret && candidate && safeEqual(candidate, secret));
}

function createSession(secret, memberId, nowMs = Date.now()) {
  if (!secret) throw new Error("FAMILY_ACCESS_TOKEN is required");
  if (!memberId) throw new Error("memberId is required");
  const payload = encode(JSON.stringify({
    version: 2,
    memberId,
    expiresAt: nowMs + SESSION_TTL_SECONDS * 1000,
  }));
  return `${payload}.${sign(payload, secret)}`;
}

function readSession(token, secret, nowMs = Date.now()) {
  if (!token || !secret) return false;
  const [payload, signature, extra] = String(token).split(".");
  if (!payload || !signature || extra || !safeEqual(signature, sign(payload, secret))) return null;
  try {
    const data = JSON.parse(Buffer.from(payload, "base64url").toString("utf8"));
    if (data.version !== 2 || !data.memberId || !Number.isFinite(data.expiresAt) || data.expiresAt <= nowMs) return null;
    return data;
  } catch {
    return null;
  }
}

function verifySession(token, secret, nowMs = Date.now()) {
  return Boolean(readSession(token, secret, nowMs));
}

function parseCookies(header = "") {
  return header.split(";").reduce((cookies, part) => {
    const separator = part.indexOf("=");
    if (separator < 0) return cookies;
    const key = part.slice(0, separator).trim();
    const value = part.slice(separator + 1).trim();
    if (key) cookies[key] = decodeURIComponent(value);
    return cookies;
  }, {});
}

function sessionFromRequest(req) {
  return parseCookies(req.headers.cookie || "")[COOKIE_NAME] || "";
}

function sessionCookie(token, secure = true) {
  return `${COOKIE_NAME}=${encodeURIComponent(token)}; Path=/; Max-Age=${SESSION_TTL_SECONDS}; HttpOnly; SameSite=Strict${secure ? "; Secure" : ""}`;
}

module.exports = {
  COOKIE_NAME,
  SESSION_TTL_SECONDS,
  createSession,
  parseCookies,
  readSession,
  sessionCookie,
  sessionFromRequest,
  validateInvite,
  verifySession,
};
