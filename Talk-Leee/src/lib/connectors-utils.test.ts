import { test } from "node:test";
import assert from "node:assert/strict";
import {
    connectorCardActionFromStatus,
    connectorCardActionLabel,
    extractAuthorizationUrl,
    formatLastSync,
    normalizeConnectorStatus,
    parseConnectorsCallback,
    pickPreferredConnector,
    summarizeConnectorStatuses,
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

test("normalizeConnectorStatus maps backend connector states", () => {
    assert.equal(normalizeConnectorStatus("active"), "connected");
    assert.equal(normalizeConnectorStatus("connected"), "connected");
    assert.equal(normalizeConnectorStatus("expired"), "expired");
    assert.equal(normalizeConnectorStatus("error"), "error");
    assert.equal(normalizeConnectorStatus("pending"), "disconnected");
});

test("pickPreferredConnector keeps a connected record over a newer pending one", () => {
    const preferred = pickPreferredConnector([
        { status: "pending", createdAt: "2025-01-02T00:00:00Z" },
        { status: "active", createdAt: "2025-01-01T00:00:00Z" },
    ]);

    assert.deepEqual(preferred, { status: "active", createdAt: "2025-01-01T00:00:00Z" });
});

test("summarizeConnectorStatuses aggregates provider records by connector type", () => {
    const statuses = summarizeConnectorStatuses([
        { id: "1", type: "calendar", provider: "google_calendar", status: "active", createdAt: "2025-01-01T00:00:00Z" },
        { id: "2", type: "calendar", provider: "outlook_calendar", status: "error", createdAt: "2025-01-02T00:00:00Z" },
        { id: "3", type: "email", provider: "gmail", status: "expired", createdAt: "2025-01-03T00:00:00Z" },
    ]);

    assert.equal(statuses.length, 2);
    assert.equal(statuses[0]!.type, "calendar");
    assert.equal(statuses[0]!.status, "connected");
    assert.equal(statuses[1]!.provider, "gmail");
});
