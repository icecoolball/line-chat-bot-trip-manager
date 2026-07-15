const test = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const {
  extractLikelySaleText,
  inspectSourceUrl,
  isPrivateAddress,
  resolvePublicHost,
  validateHttpsUrl,
} = require("../lib/source-inspect");

function fakeRequestFactory(plan, attempts) {
  return (url, opts, onResponse) => {
    const req = new EventEmitter();
    req.setTimeout = () => {};
    req.destroy = (error) => req.emit("error", error);
    req.end = () => {
      opts.lookup(url.hostname, {}, (_error, address) => attempts.push(address));
      const step = plan[attempts.length - 1];
      if (step.errorCode) {
        setImmediate(() => req.emit("error", Object.assign(new Error(`connect ${step.errorCode}`), { code: step.errorCode })));
        return;
      }
      const res = new EventEmitter();
      res.statusCode = step.statusCode || 200;
      res.headers = { date: "Sun, 12 Jul 2026 10:00:00 GMT" };
      res.resume = () => {};
      setImmediate(() => {
        onResponse(res);
        if (res.statusCode === 200) {
          res.emit("data", Buffer.from("เปิดขาย 06/07/2026 เวลา 10:00 น."));
          res.emit("end");
        }
      });
    };
    return req;
  };
}

test("rejects non-HTTPS and credential-bearing URLs", () => {
  assert.throws(() => validateHttpsUrl("http://example.com/event"), /HTTPS/);
  assert.throws(() => validateHttpsUrl("https://user:pass@example.com"), /Credentials/);
  assert.equal(validateHttpsUrl("https://example.com/event").hostname, "example.com");
});

test("detects private and reserved addresses", () => {
  for (const address of ["127.0.0.1", "10.0.0.4", "172.16.1.1", "192.168.1.2", "169.254.10.2", "::1", "fd00::1"]) {
    assert.equal(isPrivateAddress(address), true, address);
  }
  assert.equal(isPrivateAddress("8.8.8.8"), false);
  assert.equal(isPrivateAddress("2606:4700:4700::1111"), false);
});

test("rejects a hostname when any DNS answer is private", async () => {
  await assert.rejects(
    resolvePublicHost("example.com", async () => [
      { address: "93.184.216.34", family: 4 },
      { address: "127.0.0.1", family: 4 },
    ]),
    /private or reserved/,
  );
});

test("retries the next resolved address when a connection fails", async () => {
  const attempts = [];
  const result = await inspectSourceUrl("https://example.com/event", {
    lookup: async () => [
      { address: "93.184.216.34", family: 4 },
      { address: "93.184.216.35", family: 4 },
    ],
    request: fakeRequestFactory([{ errorCode: "ECONNRESET" }, {}], attempts),
  });
  assert.deepEqual(attempts, ["93.184.216.34", "93.184.216.35"]);
  assert.match(result.matchedText, /06\/07\/2026/);
});

test("prefers IPv4 addresses and does not retry HTTP errors", async () => {
  const attempts = [];
  await assert.rejects(
    inspectSourceUrl("https://example.com/event", {
      lookup: async () => [
        { address: "2606:4700:4700::1111", family: 6 },
        { address: "93.184.216.34", family: 4 },
      ],
      request: fakeRequestFactory([{ statusCode: 403 }], attempts),
    }),
    /HTTP 403/,
  );
  assert.deepEqual(attempts, ["93.184.216.34"]);
});

test("extracts sale time context without script content", () => {
  const html = `<script>เปิดขาย 01/01/1999 00:00</script><main>เปิดขาย 06/07/2026 เวลา 10:00 น.</main>`;
  const matched = extractLikelySaleText(html);
  assert.match(matched, /06\/07\/2026/);
  assert.doesNotMatch(matched, /1999/);
});
