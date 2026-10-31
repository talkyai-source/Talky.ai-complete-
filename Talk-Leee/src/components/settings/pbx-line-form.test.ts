import assert from "node:assert/strict";
import { test } from "node:test";
import type { SipTrunkRow } from "@/lib/telephony-api";
import {
    buildCreateInput,
    buildUpdatePatch,
    checkLineForm,
    directionOf,
    emptyLineForm,
    hasAdvancedValues,
    lineFormFromTrunk,
    type LineForm,
} from "./pbx-line-form";

function saved(overrides: Partial<SipTrunkRow> = {}): SipTrunkRow {
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
        metadata: { register: true, caller_id: "+10000000000", unrelated: "keep" },
        runtime_ready: false,
        runtime_status_code: "other",
        runtime_status_detail: "",
        created_at: "2026-10-02T00:00:00Z",
        ...overrides,
    };
}

function form(overrides: Partial<LineForm> = {}): LineForm {
    return { ...emptyLineForm(), address: "pbx.example.com", username: "alice", password: "secret", ...overrides };
}

function ok(f: LineForm, mode: "create" | "edit" = "create", original: SipTrunkRow | null = null) {
    const checked = checkLineForm(f, { mode, original });
    assert.equal(checked.ok, true, checked.ok ? "" : checked.error);
    if (!checked.ok) throw new Error("unreachable");
    return checked;
}

function error(f: LineForm, mode: "create" | "edit" = "create", original: SipTrunkRow | null = null) {
    const checked = checkLineForm(f, { mode, original });
    assert.equal(checked.ok, false);
    if (checked.ok) throw new Error("unreachable");
    return checked.error;
}

test("a new line starts with calls both ways, no protocol choice, and signing in on", () => {
    const f = emptyLineForm();
    assert.equal(directionOf(f), "both");
    assert.equal(f.transport, "auto");
    assert.equal(f.ipAccess, false);
    assert.equal(f.register, true);
});

test("the Edit form loads a saved line and leaves the password blank", () => {
    const f = lineFormFromTrunk(saved({ transport: "tcp", port: 5080, direction: "outbound", metadata: { caller_id: "+10000000000", srtp: true, dtmf_mode: "sip-info", outbound_proxy: "proxy.example.com:5060" } }));
    assert.equal(f.address, "sip.example.com:5080");
    assert.equal(f.transport, "tcp");
    assert.equal(f.username, "saved-user");
    assert.equal(f.password, "");
    assert.equal(f.ipAccess, false);
    assert.equal(f.callerId, "+10000000000");
    assert.deepEqual([f.makeCalls, f.receiveCalls], [true, false]);
    assert.equal(f.dtmfMode, "sip-info");
    assert.equal(f.srtp, true);
    assert.equal(f.outboundProxy, "proxy.example.com:5060");
    assert.equal(hasAdvancedValues(f), true);
    // A saved UDP line shows "Automatic", and plain settings keep Advanced closed.
    const plain = lineFormFromTrunk(saved({ auth_configured: false, auth_username: null, metadata: {} }));
    assert.equal(plain.transport, "auto");
    assert.equal(plain.ipAccess, true);
    assert.equal(plain.register, false);
    assert.equal(hasAdvancedValues(plain), false);
});

test("the provider address is required and must be an address", () => {
    assert.match(error(form({ address: "" })), /Enter the address/);
    assert.match(error(form({ address: "not a host!" })), /valid address/);
    assert.match(error(form({ port: "70000" })), /port/);
});

test("a blank line name defaults to the provider address", () => {
    assert.equal(ok(form({ name: "" , address: "sip:Pbx.Example.com:5080" })).name, "pbx.example.com");
    assert.equal(ok(form({ name: "  Head office  " })).name, "Head office");
    assert.match(error(form({ name: "ab" })), /at least 3 characters/);
});

test("at least one of Make calls and Receive calls must stay on", () => {
    assert.match(error(form({ makeCalls: false, receiveCalls: false })), /Make calls, Receive calls/);
    assert.equal(ok(form({ receiveCalls: false })).direction, "outbound");
    assert.equal(ok(form({ makeCalls: false })).direction, "inbound");
});

test("username and password are required together unless IP address access is on", () => {
    assert.match(error(form({ username: "", password: "" })), /both username and password/);
    assert.match(error(form({ password: "" })), /both username and password/);
    ok(form({ ipAccess: true, username: "", password: "" }));
});

test("editing keeps saved credentials when the username is unchanged and the password is blank", () => {
    const original = saved();
    const checked = ok(form({ address: "sip.example.com", username: "saved-user", password: "" }), "edit", original);
    assert.equal(checked.preserveAuth, true);
    const patch = buildUpdatePatch(form({ address: "sip.example.com", username: "saved-user", password: "" }), checked, original);
    assert.equal("auth_username" in patch, false);
    assert.equal("auth_password" in patch, false);
    assert.equal("clear_auth" in patch, false);
});

test("changing the username needs the password again", () => {
    const message = error(form({ address: "sip.example.com", username: "new-user", password: "" }), "edit", saved());
    assert.match(message, /both username and password/);
    assert.match(message, /Changing the username/);
    // Typing a new password for the same username is a password change and is sent.
    const f = form({ address: "sip.example.com", username: "saved-user", password: "fresh" });
    const patch = buildUpdatePatch(f, ok(f, "edit", saved()), saved());
    assert.equal(patch.auth_username, "saved-user");
    assert.equal(patch.auth_password, "fresh");
});

test("switching a saved credentials line to IP address access clears the credentials and registration", () => {
    const original = saved();
    const f = form({ address: "sip.example.com", ipAccess: true, username: "saved-user", password: "" });
    const patch = buildUpdatePatch(f, ok(f, "edit", original), original);
    assert.equal(patch.clear_auth, true);
    assert.equal("auth_password" in patch, false);
    assert.equal((patch.metadata as Record<string, unknown>).register, false);
    // An IP line that never had credentials has nothing to clear.
    const ipOriginal = saved({ auth_configured: false, auth_username: null, metadata: {} });
    const again = buildUpdatePatch(f, ok(f, "edit", ipOriginal), ipOriginal);
    assert.equal("clear_auth" in again, false);
});

test("the number to show must look like a phone number, written however the provider gave it", () => {
    const message = error(form({ callerId: "call me" }));
    assert.match(message, /number to show/);
    // The message makes no assumption about a country or a leading +.
    assert.doesNotMatch(message, /country|international|\+/i);
    // With or without a leading +, with or without separators.
    for (const callerId of ["+1 (555) 010-0100", "02079460000", "555 0100", "5550100", "", "  "]) {
        ok(form({ callerId }));
    }
});

test("the sign-in refresh time is checked only when signing in is on", () => {
    assert.match(error(form({ register: true, registerInterval: "30" })), /60 and 86400/);
    assert.match(error(form({ register: true, registerInterval: "abc" })), /60 and 86400/);
    assert.equal(ok(form({ register: true, registerInterval: "600" })).registerInterval, 600);
    ok(form({ register: false, registerInterval: "30" }));
    ok(form({ ipAccess: true, register: true, registerInterval: "30" }));
});

test("create sends the resolved connection, credentials and settings", () => {
    const f = form({ address: "sip:Sip3.Example.com:5080;transport=tcp", callerId: " +10000000000 ", name: "" });
    const input = buildCreateInput(f, ok(f));
    assert.deepEqual(input, {
        trunk_name: "sip3.example.com",
        sip_domain: "sip3.example.com",
        port: 5080,
        transport: "tcp",
        direction: "both",
        auth_username: "alice",
        auth_password: "secret",
        metadata: { caller_id: "+10000000000", register: true, register_interval: 3600, dtmf_mode: "rfc2833", srtp: false },
    });
});

test("create with IP address access sends no credentials and never registers", () => {
    const f = form({ ipAccess: true, register: true });
    const input = buildCreateInput(f, ok(f));
    assert.equal("auth_username" in input, false);
    assert.equal("auth_password" in input, false);
    assert.equal(input.metadata?.register, false);
});

test("Advanced choices override the address and blank optional text is dropped", () => {
    const f = form({ address: "pbx.example.com:5080", transport: "tls", port: "5999", authRealm: " ", outboundProxy: "proxy.example.com:5060", srtp: true, dtmfMode: "sip-info" });
    const input = buildCreateInput(f, ok(f));
    assert.equal(input.transport, "tls");
    assert.equal(input.port, 5999);
    assert.equal("auth_realm" in (input.metadata ?? {}), false);
    assert.equal(input.metadata?.outbound_proxy, "proxy.example.com:5060");
    assert.equal(input.metadata?.srtp, true);
    assert.equal(input.metadata?.dtmf_mode, "sip-info");
});

test("editing merges over the saved metadata so keys this form does not own survive", () => {
    const original = saved();
    const f = form({ address: "sip.example.com", username: "saved-user", password: "", name: "Renamed", callerId: "" });
    const patch = buildUpdatePatch(f, ok(f, "edit", original), original);
    assert.equal(patch.trunk_name, "Renamed");
    const metadata = patch.metadata as Record<string, unknown>;
    assert.equal(metadata.unrelated, "keep");
    assert.equal("caller_id" in metadata, false, "a cleared number is removed, not saved as an empty string");
    assert.equal(metadata.register, true);
});
