import assert from "node:assert/strict";
import { test } from "node:test";
import type { SipTrunkRow } from "@/lib/telephony-api";
import { deriveLineStatus, formatRelativeTime } from "./pbx-line-status";

const CHECKED = "2026-10-14T10:00:00Z";

function row(overrides: Partial<SipTrunkRow> = {}): SipTrunkRow {
    return {
        id: "trunk-1",
        tenant_id: "tenant-1",
        trunk_name: "Main line",
        sip_domain: "sip.example.com",
        port: 5060,
        transport: "udp",
        direction: "both",
        is_active: true,
        auth_configured: true,
        auth_username: "saved-user",
        metadata: {},
        runtime_ready: false,
        // Neutral by default so the LIVE state decides; tests set a stale code on purpose.
        runtime_status_code: "other",
        runtime_status_detail: "Not ready.",
        live_registration_status: null,
        live_status_checked_at: CHECKED,
        created_at: "2026-10-02T00:00:00Z",
        ...overrides,
    };
}

test("a registered line with current proof is Connected", () => {
    const status = deriveLineStatus(row({ live_registration_status: "registered", runtime_ready: true, runtime_status_code: "ready" }));
    assert.equal(status.kind, "connected");
    assert.equal(status.label, "Connected");
    assert.equal(status.detail, null);
    assert.equal(status.checkedAt, CHECKED);
});

test("an IP-address line is Connected once its contact is reachable", () => {
    const status = deriveLineStatus(row({ auth_configured: false, auth_username: null, live_registration_status: "reachable", runtime_ready: true }));
    assert.equal(status.label, "Connected");
});

test("a line that is switched off is Off whatever its last status was", () => {
    const status = deriveLineStatus(row({ is_active: false, live_registration_status: "registered", runtime_ready: true }));
    assert.equal(status.kind, "off");
    assert.equal(status.label, "Off");
    assert.equal(status.checkedAt, null, "an off line has nothing to be 'last checked'");
});

test("registered without current proof is never Connected", () => {
    // The server only grants runtime_ready from fresh evidence; a registration seen
    // once, or a successful saved test, must not read as ready for calls.
    const status = deriveLineStatus(row({
        live_registration_status: "registered",
        runtime_ready: false,
        runtime_status_code: "unreachable",
        runtime_status_detail: "No SIP response within 5s",
        last_test_result: { ok: true, detail: "SIP 200 OK", sip_code: 200 },
        last_tested_at: "2026-09-01T00:00:00Z",
    }));
    assert.equal(status.kind, "attention");
    assert.equal(status.label, "Needs attention");
    assert.equal(status.detail, "Your provider didn't answer.");
    assert.equal(status.technical, "No SIP response within 5s");
    assert.equal(status.sipCode, null, "a saved SUCCESSFUL test says nothing about now");
});

test("an out-of-date status is Connecting, not a verdict about the line", () => {
    const status = deriveLineStatus(row({ live_registration_status: "registered", runtime_status_code: "status_stale", runtime_status_detail: "Asterisk runtime status is stale (130 seconds old)." }));
    assert.equal(status.kind, "connecting");
    assert.match(status.detail ?? "", /out of date/);
    assert.equal(status.technical, null);
});

test("a rejected sign-in needs attention and carries the server's reason and SIP code", () => {
    const status = deriveLineStatus(row({
        live_registration_status: "rejected",
        live_status_detail: "403 Forbidden",
        runtime_status_code: "runtime_rejected",
        runtime_status_detail: "SIP trunk is not registered and reachable; live status is rejected (403 Forbidden).",
    }));
    assert.equal(status.kind, "attention");
    assert.equal(status.detail, "Your provider rejected the username or password.");
    assert.equal(status.technical, "403 Forbidden");
    assert.equal(status.sipCode, "403");
});

test("the 'runtime_' prefix on the server's code is ignored", () => {
    // "registered" says nothing on its own here, so the runtime code decides.
    const withPrefix = deriveLineStatus(row({ live_registration_status: "registered", runtime_status_code: "runtime_unreachable" }));
    const without = deriveLineStatus(row({ live_registration_status: "registered", runtime_status_code: "unreachable" }));
    assert.equal(withPrefix.kind, "attention");
    assert.equal(withPrefix.detail, "Your provider didn't answer.");
    assert.deepEqual(withPrefix, without);
});

test("lines still checking or signing in are Connecting", () => {
    for (const live of ["checking", "registering", "loaded", "unknown", "inactive"]) {
        const status = deriveLineStatus(row({ live_registration_status: live }));
        assert.equal(status.kind, "connecting", live);
        assert.equal(status.label, "Connecting");
    }
});

test("every provider-side failure state needs attention with its own plain sentence", () => {
    const sentences = new Map<string, string>();
    for (const live of ["rejected", "failed", "unregistered", "stopped", "unreachable", "missing_config"]) {
        const status = deriveLineStatus(row({ live_registration_status: live }));
        assert.equal(status.kind, "attention", live);
        assert.ok(status.detail && status.detail.length > 10, live);
        sentences.set(live, status.detail ?? "");
    }
    assert.equal(new Set(sentences.values()).size, sentences.size, "each state has its own sentence");
});

test("a state the page does not know is still not Connected and shows the server's wording", () => {
    const status = deriveLineStatus(row({ live_registration_status: "weird_state", runtime_status_detail: "The provider said something new." }));
    assert.equal(status.kind, "attention");
    assert.equal(status.detail, "This line isn't ready for calls.");
    assert.equal(status.technical, "The provider said something new.");
});

test("a line with no status at all is Not checked", () => {
    for (const live of [null, undefined, ""]) {
        const status = deriveLineStatus(row({ live_registration_status: live, live_status_checked_at: null }));
        assert.equal(status.kind, "unchecked");
        assert.equal(status.label, "Not checked");
    }
});

test("an inbound-only line is judged by inbound proof, not by the outbound probe", () => {
    const base = { direction: "inbound" as const, live_registration_status: "unreachable", runtime_ready: false };
    assert.equal(deriveLineStatus(row({ ...base, inbound_runtime_ready: true })).label, "Connected");
    assert.equal(deriveLineStatus(row({ ...base, inbound_runtime_ready: false })).label, "Needs attention");
    // Missing on an older response: never infer inbound readiness.
    assert.equal(deriveLineStatus(row({ ...base })).label, "Needs attention");
    // An outbound-capable line ignores inbound proof.
    assert.equal(deriveLineStatus(row({ ...base, direction: "both", inbound_runtime_ready: true })).label, "Needs attention");
});

test("a saved FAILED test can supply the SIP code when the live detail has none", () => {
    const status = deriveLineStatus(row({
        live_registration_status: "unregistered",
        last_test_result: { ok: false, sip_code: 408, detail: "timed out" },
    }));
    assert.equal(status.sipCode, "408");
});

test("relative time reads naturally and never guesses", () => {
    const now = Date.parse("2026-10-14T12:00:00Z");
    const ago = (seconds: number) => new Date(now - seconds * 1000).toISOString();
    assert.equal(formatRelativeTime(ago(5), now), "just now");
    assert.equal(formatRelativeTime(ago(29), now), "just now");
    assert.equal(formatRelativeTime(ago(45), now), "45 seconds ago");
    assert.equal(formatRelativeTime(ago(60), now), "1 minute ago");
    assert.equal(formatRelativeTime(ago(150), now), "2 minutes ago");
    assert.equal(formatRelativeTime(ago(3600), now), "1 hour ago");
    assert.equal(formatRelativeTime(ago(7300), now), "2 hours ago");
    assert.equal(formatRelativeTime(ago(86400), now), "1 day ago");
    assert.equal(formatRelativeTime(ago(3 * 86400), now), "3 days ago");
    assert.equal(formatRelativeTime(new Date(now + 60_000).toISOString(), now), "just now", "a clock a little ahead is not 'in the future'");
    assert.equal(formatRelativeTime(null, now), null);
    assert.equal(formatRelativeTime(undefined, now), null);
    assert.equal(formatRelativeTime("not a date", now), null);
    assert.equal(formatRelativeTime(ago(5), 0), null, "before the clock is known (server render)");
});
