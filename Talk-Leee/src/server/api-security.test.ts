import { test } from "node:test";
import assert from "node:assert/strict";
import crypto from "node:crypto";
import { parseStripeSignatureHeader, sanitizeUnknown, sha256Hex, verifyStripeWebhookSignature } from "@/server/api-security";

test("sanitizeUnknown removes prototype pollution keys", () => {
    const input = JSON.parse(`{"ok":true,"__proto__":{"polluted":true},"constructor":{"x":1},"nested":{"prototype":{"y":2},"name":"a\\u0000b"}}`) as unknown;
    const out = sanitizeUnknown(input) as Record<string, unknown>;
    assert.equal(out.ok, true);
    assert.equal(Object.prototype.hasOwnProperty.call(out, "__proto__"), false);
    assert.equal(Object.prototype.hasOwnProperty.call(out, "constructor"), false);
    const nested = out.nested as Record<string, unknown>;
    assert.equal(Object.prototype.hasOwnProperty.call(nested, "prototype"), false);
    assert.equal(nested.name, "ab");
});

test("parseStripeSignatureHeader extracts timestamp and v1 signatures", () => {
    const header = "t=1710000000,v1=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa,v1=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    const parsed = parseStripeSignatureHeader(header);
    assert.ok(parsed);
    assert.equal(parsed.timestamp, 1710000000);
    assert.deepEqual(parsed.signatures, [
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    ]);
});

test("verifyStripeWebhookSignature accepts valid signature and rejects invalid", () => {
    const secret = crypto.randomBytes(16).toString("hex");
    const timestamp = Math.floor(Date.now() / 1000);
    const raw = Buffer.from(JSON.stringify({ id: "evt_123", type: "test" }), "utf8");
    const signedPayload = Buffer.concat([Buffer.from(String(timestamp) + ".", "utf8"), raw]);
    const sig = crypto.createHmac("sha256", secret).update(signedPayload).digest("hex");
    const header = `t=${timestamp},v1=${sig}`;

    const ok = verifyStripeWebhookSignature({ rawBody: raw, header, secret, toleranceSeconds: 300 });
    assert.equal(ok.ok, true);

    const bad = verifyStripeWebhookSignature({ rawBody: raw, header: `t=${timestamp},v1=${sha256Hex("nope")}`, secret, toleranceSeconds: 300 });
    assert.equal(bad.ok, false);
});

