import { test } from "node:test";
import assert from "node:assert/strict";
import {
    connectorCardActionFromStatus,
    connectorCardActionLabel,
    extractAuthorizationUrl,
    parseConnectorsCallback,
    formatLastSync,
} from "@/lib/connectors-utils";

test("connectorCardActionFromStatus maps all states", () => {
    assert.equal(connectorCardActionFromStatus("connected"), "disconnect");
    assert.equal(connectorCardActionFromStatus("disconnected"), "connect");
    assert.equal(connectorCardActionFromStatus("expired"), "reconnect");
    assert.equal(connectorCardActionFromStatus("error"), "reconnect");
    assert.equal(connectorCardActionFromStatus("unknown"), "connect");
});

test("connectorCardActionLabel renders user-facing labels", () => {
    assert.equal(connectorCardActionLabel("connect"), "Connect");
    assert.equal(connectorCardActionLabel("reconnect"), "Reconnect");
    assert.equal(connectorCardActionLabel("disconnect"), "Disconnect");
});

test("extractAuthorizationUrl accepts multiple response shapes", () => {
    assert.equal(extractAuthorizationUrl(" https://example.com/auth "), "https://example.com/auth");
    assert.equal(extractAuthorizationUrl({ authorization_url: "https://example.com/a" }), "https://example.com/a");
    assert.equal(extractAuthorizationUrl({ url: "https://example.com/b" }), "https://example.com/b");
    assert.throws(() => extractAuthorizationUrl({}), /Authorization failed/i);
});

test("parseConnectorsCallback handles success and failure", () => {
    const ok = parseConnectorsCallback(new URLSearchParams("status=success&type=calendar"));
    assert.equal(ok.ok, true);
    assert.equal(ok.providerType, "calendar");
    assert.match(ok.message, /connected successfully/i);

    const fail = parseConnectorsCallback(new URLSearchParams("status=error&type=email&error=denied"));
    assert.equal(fail.ok, false);
    assert.equal(fail.providerType, "email");
    assert.equal(fail.message, "denied");
});

test("parseConnectorsCallback uses default providerType when missing from query", () => {
    const ok = parseConnectorsCallback(new URLSearchParams("status=success"), "drive");
    assert.equal(ok.ok, true);
    assert.equal(ok.providerType, "drive");
});

test("formatLastSync is stable for empty and invalid values", () => {
    assert.equal(formatLastSync(undefined), "—");
    assert.equal(formatLastSync(null), "—");
    assert.equal(formatLastSync("not-a-date"), "not-a-date");
    assert.ok(formatLastSync("2025-01-01T00:00:00Z").length > 0);
});
