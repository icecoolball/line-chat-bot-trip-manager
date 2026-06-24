require("dotenv").config();

const fs = require("fs");
const http = require("http");
const path = require("path");
const { createClient } = require("@supabase/supabase-js");
const {
  createSession,
  sessionCookie,
  sessionFromRequest,
  validateInvite,
  verifySession,
} = require("./lib/auth");
const { inspectSourceUrl, validateHttpsUrl } = require("./lib/source-inspect");
const { createScheduleStore } = require("./lib/schedule-store");

const PUBLIC_ROOT = path.join(__dirname, "public");
const MAX_JSON_BYTES = 64 * 1024;
const LOGIN_WINDOW_MS = 15 * 60 * 1000;
const LOGIN_MAX_FAILURES = 5;
const STATIC_TYPES = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
};

function securityHeaders(extra = {}) {
  return {
    "cache-control": "no-store",
    "content-security-policy": "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
    "referrer-policy": "no-referrer",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    ...extra,
  };
}

function sendJson(res, status, body, headers = {}) {
  res.writeHead(status, securityHeaders({ "content-type": "application/json; charset=utf-8", ...headers }));
  res.end(JSON.stringify(body));
}

function readJson(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > MAX_JSON_BYTES) {
        const error = new Error("Request body is too large");
        error.statusCode = 413;
        reject(error);
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => {
      try {
        resolve(chunks.length ? JSON.parse(Buffer.concat(chunks).toString("utf8")) : {});
      } catch {
        const error = new Error("Invalid JSON body");
        error.statusCode = 400;
        reject(error);
      }
    });
    req.on("error", reject);
  });
}

function clientAddress(req) {
  return String(req.headers["x-forwarded-for"] || req.socket.remoteAddress || "unknown").split(",")[0].trim();
}

function isSecureRequest(req) {
  return process.env.NODE_ENV === "production" || req.headers["x-forwarded-proto"] === "https";
}

function validateSchedule(payload) {
  const name = String(payload.name || "").trim();
  const site = String(payload.site || "").trim();
  const url = validateHttpsUrl(String(payload.url || "").trim()).toString();
  const saleAtDate = new Date(payload.saleAt);
  if (!name || name.length > 120) throw Object.assign(new Error("Event name must be 1-120 characters"), { statusCode: 400 });
  if (!site || site.length > 80) throw Object.assign(new Error("Site must be 1-80 characters"), { statusCode: 400 });
  if (!Number.isFinite(saleAtDate.getTime())) throw Object.assign(new Error("Invalid sale time"), { statusCode: 400 });
  const now = Date.now();
  if (saleAtDate.getTime() <= now || saleAtDate.getTime() > now + 2 * 365 * 24 * 60 * 60 * 1000) {
    throw Object.assign(new Error("Sale time must be in the next two years"), { statusCode: 400 });
  }
  return { name, site, url, saleAt: saleAtDate.toISOString() };
}

function createRequestHandler(options) {
  const familyAccessToken = options.familyAccessToken;
  const scheduleStore = options.scheduleStore;
  const sourceInspector = options.sourceInspector || inspectSourceUrl;
  const loginFailures = new Map();

  return async function requestHandler(req, res) {
    try {
      const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);

      if (url.pathname === "/healthz" && req.method === "GET") {
        return sendJson(res, 200, { ok: true });
      }

      if (url.pathname === "/api/session/invite" && req.method === "POST") {
        const address = clientAddress(req);
        const state = loginFailures.get(address);
        if (state && state.blockedUntil > Date.now()) {
          return sendJson(res, 429, { ok: false, error: "Too many attempts. Try again later." });
        }
        const body = await readJson(req);
        if (!validateInvite(body.token, familyAccessToken)) {
          const count = state && state.windowStart > Date.now() - LOGIN_WINDOW_MS ? state.count + 1 : 1;
          loginFailures.set(address, {
            count,
            windowStart: count === 1 ? Date.now() : state.windowStart,
            blockedUntil: count >= LOGIN_MAX_FAILURES ? Date.now() + LOGIN_WINDOW_MS : 0,
          });
          return sendJson(res, 401, { ok: false, error: "Invalid invite link" });
        }
        loginFailures.delete(address);
        const token = createSession(familyAccessToken);
        return sendJson(res, 200, { ok: true }, { "set-cookie": sessionCookie(token, isSecureRequest(req)) });
      }

      const sessionValid = verifySession(sessionFromRequest(req), familyAccessToken);
      if (url.pathname === "/api/session" && req.method === "GET") {
        return sendJson(res, sessionValid ? 200 : 401, { ok: sessionValid });
      }

      if (url.pathname.startsWith("/api/") && !sessionValid) {
        return sendJson(res, 401, { ok: false, error: "Invite access is required" });
      }

      if (url.pathname === "/api/source-inspect" && req.method === "POST") {
        const body = await readJson(req);
        const result = await sourceInspector(body.url);
        return sendJson(res, 200, { ok: true, ...result });
      }

      if (url.pathname === "/api/schedules" && req.method === "GET") {
        return sendJson(res, 200, { ok: true, schedules: await scheduleStore.list() });
      }

      if (url.pathname === "/api/schedules" && req.method === "POST") {
        const payload = validateSchedule(await readJson(req));
        return sendJson(res, 201, { ok: true, schedule: await scheduleStore.create(payload) });
      }

      if (url.pathname.startsWith("/api/schedules/") && req.method === "DELETE") {
        const id = decodeURIComponent(url.pathname.slice("/api/schedules/".length));
        if (!/^[0-9a-f-]{36}$/i.test(id)) return sendJson(res, 400, { ok: false, error: "Invalid schedule id" });
        await scheduleStore.remove(id);
        return sendJson(res, 200, { ok: true });
      }

      if (url.pathname.startsWith("/api/")) return sendJson(res, 404, { ok: false, error: "Not found" });

      const requested = url.pathname === "/" ? "index.html" : decodeURIComponent(url.pathname).replace(/^\/+/, "");
      const filePath = path.resolve(PUBLIC_ROOT, requested);
      const relative = path.relative(PUBLIC_ROOT, filePath);
      if (relative.startsWith("..") || path.isAbsolute(relative)) return sendJson(res, 403, { ok: false, error: "Forbidden" });

      fs.readFile(filePath, (error, content) => {
        if (error) {
          res.writeHead(404, securityHeaders({ "content-type": "text/plain; charset=utf-8" }));
          res.end("Not found");
          return;
        }
        res.writeHead(200, securityHeaders({ "content-type": STATIC_TYPES[path.extname(filePath)] || "application/octet-stream" }));
        if (req.method === "HEAD") res.end(); else res.end(content);
      });
    } catch (error) {
      console.error("Request failed:", error.message);
      const statusCode = error.statusCode || 500;
      const exposeMessage = Boolean(error.statusCode) || req.url === "/api/source-inspect" || req.url.startsWith("/api/source-inspect?");
      sendJson(res, statusCode, { ok: false, error: exposeMessage ? error.message : "Internal server error" });
    }
  };
}

function createRuntimeHandler(env = process.env) {
  const required = ["FAMILY_ACCESS_TOKEN", "SUPABASE_URL", "SUPABASE_ANON_KEY", "TICKET_BACKEND_TOKEN"];
  const missing = required.filter((name) => !env[name]);
  if (missing.length) throw new Error(`Missing required environment variables: ${missing.join(", ")}`);
  const supabase = createClient(env.SUPABASE_URL, env.SUPABASE_ANON_KEY, {
    auth: { autoRefreshToken: false, persistSession: false },
  });
  return createRequestHandler({
    familyAccessToken: env.FAMILY_ACCESS_TOKEN,
    scheduleStore: createScheduleStore(supabase, env.TICKET_BACKEND_TOKEN),
  });
}

if (require.main === module) {
  const port = Number(process.env.PORT || 5177);
  http.createServer(createRuntimeHandler()).listen(port, () => {
    console.log(`ticket-concert listening on http://localhost:${port}`);
  });
}

module.exports = { createRequestHandler, createRuntimeHandler, readJson, validateSchedule };
