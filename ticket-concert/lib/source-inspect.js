const dns = require("dns").promises;
const https = require("https");
const net = require("net");

const MAX_BYTES = 512 * 1024;
const MAX_REDIRECTS = 3;
const TIMEOUT_MS = 8000;

function isPrivateIpv4(address) {
  const parts = address.split(".").map(Number);
  if (parts.length !== 4 || parts.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) return true;
  const [a, b] = parts;
  return a === 0 || a === 10 || a === 127 ||
    (a === 100 && b >= 64 && b <= 127) ||
    (a === 169 && b === 254) ||
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && (b === 0 || b === 168)) ||
    (a === 198 && (b === 18 || b === 19 || b === 51)) ||
    (a === 203 && b === 0) || a >= 224;
}

function isPrivateAddress(address) {
  const normalized = String(address).toLowerCase().split("%")[0];
  if (net.isIPv4(normalized)) return isPrivateIpv4(normalized);
  if (!net.isIPv6(normalized)) return true;
  if (normalized.startsWith("::ffff:")) return isPrivateIpv4(normalized.slice(7));
  return normalized === "::" || normalized === "::1" || normalized.startsWith("fc") ||
    normalized.startsWith("fd") || /^fe[89ab]/.test(normalized) || normalized.startsWith("2001:db8:");
}

async function resolvePublicHost(hostname, lookup = dns.lookup) {
  if (net.isIP(hostname)) {
    if (isPrivateAddress(hostname)) throw new Error("Private or reserved IP addresses are not allowed");
    return [{ address: hostname, family: net.isIP(hostname) }];
  }
  const addresses = await lookup(hostname, { all: true, verbatim: true });
  if (!addresses.length || addresses.some(({ address }) => isPrivateAddress(address))) {
    throw new Error("Host resolves to a private or reserved address");
  }
  return addresses;
}

function validateHttpsUrl(rawUrl) {
  let url;
  try { url = new URL(rawUrl); } catch { throw new Error("Invalid URL"); }
  if (url.protocol !== "https:") throw new Error("Only HTTPS URLs are allowed");
  if (url.username || url.password) throw new Error("Credentials in URLs are not allowed");
  return url;
}

function decodeHtml(value) {
  return value
    .replace(/&nbsp;/gi, " ").replace(/&amp;/gi, "&").replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'").replace(/&lt;/gi, "<").replace(/&gt;/gi, ">");
}

function extractLikelySaleText(html) {
  const text = decodeHtml(String(html)
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, " ")
    .replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " "));
  const patterns = [
    /(?:เปิดขาย|จำหน่าย|sale|on sale)[^.!?\n]{0,120}(?:\d{1,4}[-/. ]\d{1,2}[-/. ]\d{1,4}|\d{1,2}\s+[A-Za-zก-๙.]+\s+\d{2,4})[^.!?\n]{0,80}/i,
    /(?:\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}|\d{1,2}\s+[A-Za-zก-๙.]+\s+\d{2,4})[^.!?\n]{0,60}\d{1,2}[:.]\d{2}(?::\d{2})?/i,
  ];
  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (match) return match[0].trim().slice(0, 240);
  }
  return "";
}

async function inspectSourceUrl(rawUrl, options = {}, redirects = 0) {
  if (redirects > MAX_REDIRECTS) throw new Error("Too many redirects");
  const url = validateHttpsUrl(rawUrl);
  const addresses = await resolvePublicHost(url.hostname, options.lookup || dns.lookup);
  const pinned = addresses[0];
  const requestImpl = options.request || https.request;

  return new Promise((resolve, reject) => {
    const req = requestImpl(url, {
      method: "GET",
      headers: { "User-Agent": "ticket-concert-source-inspector/1.0", Accept: "text/html,text/plain;q=0.9,*/*;q=0.1" },
      lookup: (_hostname, _options, callback) => callback(null, pinned.address, pinned.family),
    }, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        res.resume();
        const next = new URL(res.headers.location, url).toString();
        inspectSourceUrl(next, options, redirects + 1).then(resolve, reject);
        return;
      }
      if (res.statusCode < 200 || res.statusCode >= 300) {
        res.resume();
        reject(new Error(`Source returned HTTP ${res.statusCode}`));
        return;
      }
      let size = 0;
      const chunks = [];
      res.on("data", (chunk) => {
        size += chunk.length;
        if (size > MAX_BYTES) {
          req.destroy(new Error("Source response is too large"));
          return;
        }
        chunks.push(chunk);
      });
      res.on("end", () => resolve({
        finalUrl: url.toString(),
        sourceDate: res.headers.date || null,
        matchedText: extractLikelySaleText(Buffer.concat(chunks).toString("utf8")),
      }));
    });
    req.setTimeout(options.timeoutMs || TIMEOUT_MS, () => req.destroy(new Error("Source request timed out")));
    req.on("error", reject);
    req.end();
  });
}

module.exports = { extractLikelySaleText, inspectSourceUrl, isPrivateAddress, resolvePublicHost, validateHttpsUrl };
