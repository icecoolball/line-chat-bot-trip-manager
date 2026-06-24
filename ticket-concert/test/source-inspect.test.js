const test = require("node:test");
const assert = require("node:assert/strict");
const {
  extractLikelySaleText,
  isPrivateAddress,
  resolvePublicHost,
  validateHttpsUrl,
} = require("../lib/source-inspect");

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

test("extracts sale time context without script content", () => {
  const html = `<script>เปิดขาย 01/01/1999 00:00</script><main>เปิดขาย 06/07/2026 เวลา 10:00 น.</main>`;
  const matched = extractLikelySaleText(html);
  assert.match(matched, /06\/07\/2026/);
  assert.doesNotMatch(matched, /1999/);
});
