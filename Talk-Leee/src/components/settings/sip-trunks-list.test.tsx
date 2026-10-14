import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, configure, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { api } from "@/lib/api";
import { ApiClientError } from "@/lib/http-client";
import { notificationsStore } from "@/lib/notifications";
import type { SipTrunkRow, SipTrunkTestResponse } from "@/lib/telephony-api";
import { SipTrunksList } from "./sip-trunks-list";

type Req = Parameters<typeof api.request>[0];

const request = api.request;
const clients: QueryClient[] = [];
let mounts = 0;
// jsdom has no layout engine; the shared Select scrolls its active option.
if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};
// The first render of this module graph is slow on a loaded machine.
configure({ asyncUtilTimeout: 5000 });
afterEach(() => {
    cleanup();
    clients.splice(0).forEach((client) => client.clear());
    api.request = request;
    notificationsStore.setIdentity(null);
    // Notification history is persisted per identity; never let one test read another's.
    window.localStorage.clear();
});

const IP_ACCESS = "My provider allows our server by IP address (no username or password)";

function trunk(overrides: Partial<SipTrunkRow> = {}): SipTrunkRow {
    return { id: "trunk-1", tenant_id: "tenant-1", trunk_name: "Test trunk", sip_domain: "sip.example.com", port: 5060, transport: "udp", direction: "both", is_active: true, auth_configured: true, auth_username: "saved-user", metadata: { register: true, caller_id: "+442079460000", unrelated: "keep" }, runtime_ready: false, runtime_status_code: "unreachable", runtime_status_detail: "No SIP response within 5s", live_registration_status: "registered", created_at: "2026-10-02T00:00:00Z", ...overrides };
}

function testResponse(overrides: Partial<SipTrunkTestResponse> = {}): SipTrunkTestResponse {
    return {
        ok: true, status: "connected", latency_ms: 42, transport: "udp", target: "sip.example.com:5060", tested_at: new Date().toISOString(),
        verdict: "Connected. Your provider accepted the sign-in.",
        steps: [
            { key: "address", ok: true, label: "Address found", detail: "sip.example.com points to 203.0.113.10." },
            { key: "reachability", ok: true, label: "Provider answered", detail: "Replied in 42 ms." },
            { key: "credentials", ok: true, label: "Sign-in accepted", detail: "The username and password were accepted." },
            { key: "registration", ok: null, label: "Registration", detail: "Skipped for this line." },
        ],
        ...overrides,
    };
}

/** An error the way the shared client reports an `application/problem+json` response. */
function problem(status: number, body: { title: string; detail: string }) {
    return new ApiClientError({ status, code: status === 429 ? "rate_limited" : "http_error", message: "Request failed", details: JSON.stringify({ type: "t", status, ...body }), url: "http://x/telephony/sip/trunks", method: "POST" });
}

interface MountState { rows: SipTrunkRow[]; gets: string[]; writes: Req[] }

function mount(options: {
    rows?: SipTrunkRow[];
    route?: unknown;
    pool?: unknown[];
    listError?: Error;
    /** Answers every non-GET request. Set `state.rows` here to change what the next refresh returns. */
    write?: (req: Req, state: MountState) => Promise<unknown>;
} = {}): MountState {
    const state: MountState = { rows: options.rows ?? [trunk()], gets: [], writes: [] };
    notificationsStore.setIdentity({ tenantId: "tenant-1", userId: `user-${++mounts}` });
    api.request = async <T,>(req: Req) => {
        if (req.method && req.method !== "GET") {
            state.writes.push(req);
            return await (options.write ?? (async () => ({})))(req, state) as T;
        }
        state.gets.push(req.path);
        if (req.path.endsWith("/outbound-route")) return (options.route ?? {}) as T;
        if (req.path.endsWith("/pool")) return (options.pool ?? []) as T;
        if (req.path.endsWith("/pool-assignment")) return {} as T;
        if (options.listError) throw options.listError;
        return state.rows as T;
    };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false, gcTime: 0 } } });
    clients.push(client);
    render(<QueryClientProvider client={client}><SipTrunksList /></QueryClientProvider>);
    return state;
}

async function line(name: string) {
    const heading = await screen.findByRole("heading", { name });
    return within(heading.closest("li") as HTMLElement);
}

/**
 * A "this is not on the page" check. Never use assert.equal(node, null): when it fails inside waitFor
 * (the usual case while something is still closing) assert builds its message by inspecting the whole
 * jsdom node, which takes minutes and can run out of memory.
 */
function absent(node: Element | null, message = "it should not be on the page") {
    assert.ok(node === null, message);
}

function calls(state: MountState) {
    return state.writes.map((req) => `${req.method} ${req.path}`);
}

function notified(title: string) {
    return notificationsStore.getSnapshot().notifications.find((item) => item.title === title);
}

function type(label: string, value: string) {
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

async function openConnectDialog() {
    fireEvent.click(await screen.findByRole("button", { name: "Connect PBX" }));
    await screen.findByRole("dialog", { name: "Connect your PBX" });
}

// ---------------------------------------------------------------------------------------------
// The list: one card per line, with a plain status
// ---------------------------------------------------------------------------------------------

test("registration and a successful old probe do not claim calling readiness", async () => {
    mount({ rows: [trunk({ last_test_result: { ok: true, detail: "SIP 200 OK" }, last_tested_at: "2026-09-01T00:00:00Z" })] });
    const card = await line("Test trunk");
    card.getByText("Needs attention");
    absent(card.queryByText("Connected"));
    card.getByText("Your provider didn't answer.");
    card.getByText("No SIP response within 5s");
    assert.equal(card.getByRole("switch", { name: "Use this line (Test trunk)" }).getAttribute("aria-checked"), "true");
});

test("each line shows one plain status: Connected, Needs attention, Off, Connecting or Not checked", async () => {
    const fresh = new Date().toISOString();
    mount({
        rows: [
            trunk({ id: "a", trunk_name: "Registered line", live_registration_status: "registered", runtime_ready: true, runtime_status_code: "ready", live_status_checked_at: fresh }),
            trunk({ id: "b", trunk_name: "Rejected line", live_registration_status: "rejected", live_status_detail: "403 Forbidden", runtime_status_code: "runtime_rejected", live_status_checked_at: fresh }),
            trunk({ id: "c", trunk_name: "Idle line", is_active: false, live_registration_status: "inactive", runtime_status_code: "inactive" }),
            trunk({ id: "d", trunk_name: "Starting line", live_registration_status: "checking", runtime_status_code: "runtime_checking" }),
            trunk({ id: "e", trunk_name: "Fresh line", live_registration_status: null, runtime_status_code: "status_missing" }),
        ],
    });

    const registered = await line("Registered line");
    registered.getByText("Connected");
    registered.getByText("Last checked just now");

    const rejected = await line("Rejected line");
    rejected.getByText("Needs attention");
    rejected.getByText("Your provider rejected the username or password.");
    rejected.getByText("403 Forbidden");
    rejected.getByText("Last checked just now · SIP 403");

    const idle = await line("Idle line");
    idle.getByText("Off");
    idle.getByText("Switched off. Turn it on to use this line for calls.");
    assert.equal(idle.getByRole("switch", { name: "Use this line (Idle line)" }).getAttribute("aria-checked"), "false");

    const starting = await line("Starting line");
    starting.getByText("Connecting");
    starting.getByText("Connecting to your provider.");

    const unchecked = await line("Fresh line");
    unchecked.getByText("Not checked");
    for (const [name, other] of [["Registered line", "Needs attention"], ["Rejected line", "Connected"], ["Idle line", "Connected"], ["Starting line", "Needs attention"], ["Fresh line", "Connected"]] as const) {
        absent((await line(name)).queryByText(other), `${name} must not read as "${other}"`);
    }
});

test("each card names the server, how it signs in, and what the line does", async () => {
    mount({ rows: [trunk({ transport: "tls", port: 5061, direction: "outbound" })] });
    const card = await line("Test trunk");
    card.getByText("sip.example.com:5061 · TLS");
    card.getByText("Username: saved-user");
    card.getByText("Makes calls only");
});

test("IP trunk can be ready without registration or credentials", async () => {
    mount({ rows: [trunk({ auth_configured: false, auth_username: null, metadata: { register: false }, live_registration_status: "reachable", runtime_ready: true, runtime_status_detail: "Outbound contact available" })] });
    const card = await line("Test trunk");
    card.getByText("Connected");
    card.getByText("Signs in by IP address");
});

test("an inbound-only IP trunk uses inbound proof despite an unanswered outbound probe", async () => {
    mount({ rows: [trunk({ direction: "inbound", auth_configured: false, auth_username: null, runtime_ready: false, inbound_runtime_ready: true, live_registration_status: "unreachable" })] });
    const card = await line("Test trunk");
    card.getByText("Connected");
    card.getByText("Receives calls only");
    absent(card.queryByText("Needs attention"));
});

test("the page shows the number outbound calls really go out from, not only a stored caller ID", async () => {
    // Owner report 2026-10-14: the backend dialled one number while this page
    // kept showing another (the trunk's stored caller ID).
    mount({ route: { ready: true, reason_code: "ready", caller_id: "+17789249977", trunk_id: "trunk-1", trunk_name: "Test trunk" } });
    const status = await screen.findByText(/Outbound calls go out from/);
    assert.match(status.textContent || "", /\+17789249977 on Test trunk/);
    assert.ok(screen.getByText("Outbound calls use this line"));
    assert.ok(screen.getByText("Number shown on calls: +442079460000"));
});

test("a line the dialer cannot use says so instead of showing a number as if it worked", async () => {
    mount({ route: { ready: false, reason_code: "selected_trunk_not_ready", reason: "Selected SIP trunk is not ready (selected_trunk_not_ready). Check its live status in Settings.", caller_id: "+442046132300", trunk_id: "trunk-1" } });
    const status = await screen.findByText(/Outbound calls can.t go out right now/);
    assert.match(status.textContent || "", /not ready/);
    assert.match(status.textContent || "", /\+442046132300/);
    absent(screen.queryByText("Outbound calls use this line"));
});

test("an account that does not dial through SIP lines shows no outbound line summary", async () => {
    mount({ route: { ready: true, reason_code: "sip_not_required", caller_id: "1001", trunk_id: null } });
    await screen.findByText("Test trunk");
    absent(screen.queryByText(/Outbound calls go out from/));
    absent(screen.queryByText(/1001/));
});

test("with no lines the page explains how to connect one", async () => {
    mount({ rows: [] });
    await screen.findByText(/No PBX connected yet/);
    assert.ok(screen.getByRole("button", { name: "Connect PBX" }));
});

test("a list that cannot load says so and can be retried", async () => {
    const state = mount({ listError: new Error("network down") });
    await screen.findByText(/Could not load your PBX connections/);
    const before = state.gets.filter((path) => path.endsWith("/trunks")).length;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => assert.ok(state.gets.filter((path) => path.endsWith("/trunks")).length > before));
});

test("a shared pool account can be assigned from the page, and unavailable ones cannot", async () => {
    const pool = [
        { id: "pool-1", label: "Pool 150001", caller_id: "+10000000001", registration_status: "registered", runtime_ready: true, runtime_status_detail: "ok" },
        { id: "pool-2", label: "Pool 150002", caller_id: null, registration_status: "rejected", runtime_ready: false, runtime_status_detail: "No proof" },
    ];
    const state = mount({ pool, write: async () => ({ pool_trunk_id: "pool-1", label: "Pool 150001" }) });
    const selector = await screen.findByRole("combobox", { name: "Shared trunk account" }) as HTMLButtonElement;
    // The selector shows (disabled) while the pool loads; open it once it is usable.
    await waitFor(() => assert.equal(selector.disabled, false));
    fireEvent.click(selector);
    const unavailable = screen.getByRole("option", { name: "Pool 150002 · rejected · unavailable: No proof" }) as HTMLButtonElement;
    assert.equal(unavailable.disabled, true);
    fireEvent.click(screen.getByRole("option", { name: "Pool 150001 · +10000000001" }));
    await waitFor(() => assert.equal(state.writes.length, 1));
    assert.deepEqual(calls(state), ["PUT /telephony/sip/trunks/pool-assignment"]);
    assert.deepEqual(state.writes[0].body, { pool_trunk_id: "pool-1" });
    await waitFor(() => assert.ok(notified("Shared account assigned")));
});

// ---------------------------------------------------------------------------------------------
// Connect your PBX (create)
// ---------------------------------------------------------------------------------------------

test("connecting a PBX sends the parsed address, then tests the new line automatically", async () => {
    const created = trunk({ id: "trunk-new", trunk_name: "sip3.example.com", sip_domain: "sip3.example.com", port: 5080, transport: "tcp", is_active: false, live_registration_status: "inactive", runtime_status_code: "inactive" });
    const state = mount({
        rows: [],
        write: async (req, s) => {
            if (req.path.endsWith("/test")) return testResponse();
            s.rows = [created];
            return created;
        },
    });
    await openConnectDialog();
    type("Provider address", "sip:Sip3.Example.com:5080;transport=tcp");
    screen.getByText("sip3.example.com · port 5080 · TCP");
    type("Username", "alice");
    type("Password", "s3cret");
    type("Number to show when calling", "+1 555 0100");
    fireEvent.click(screen.getByRole("button", { name: "Save and test" }));

    await waitFor(() => assert.equal(state.writes.length, 2));
    const [create, probe] = state.writes;
    assert.equal(create.method, "POST");
    assert.equal(create.path, "/telephony/sip/trunks");
    assert.ok(create.headers?.["Idempotency-Key"]);
    assert.deepEqual(create.body, {
        trunk_name: "sip3.example.com",
        sip_domain: "sip3.example.com",
        port: 5080,
        transport: "tcp",
        direction: "both",
        auth_username: "alice",
        auth_password: "s3cret",
        metadata: { caller_id: "+1 555 0100", register: true, register_interval: 3600, dtmf_mode: "rfc2833", srtp: false },
    });
    // The test runs against the line the server just created, with no click.
    assert.equal(probe.method, "POST");
    assert.equal(probe.path, "/telephony/sip/trunks/trunk-new/test");
    assert.ok(probe.headers?.["Idempotency-Key"]);

    await waitFor(() => absent(screen.queryByRole("dialog")));
    const card = await line("sip3.example.com");
    await card.findByText("Connected. Your provider accepted the sign-in.");
    assert.ok(notified("PBX connection saved"));
});

test("the number to show makes no assumption about a country or a leading +", async () => {
    mount({ rows: [] });
    await openConnectDialog();
    const field = screen.getByLabelText("Number to show when calling") as HTMLInputElement;
    assert.equal(field.placeholder, "Optional — the number your provider gave you");
    const text = screen.getByRole("dialog", { name: "Connect your PBX" }).textContent ?? "";
    assert.doesNotMatch(`${text} ${field.placeholder}`, /country|international|\+/i);
    // A number with no + is accepted exactly as typed.
    type("Number to show when calling", "02079460000");
    type("Provider address", "pbx.example.com");
    type("Username", "alice");
    type("Password", "s3cret");
    assert.equal(field.value, "02079460000");
});

test("the address box explains what it understood as you type", async () => {
    mount({ rows: [] });
    await openConnectDialog();
    type("Provider address", "pbx.example.com");
    screen.getByText("pbx.example.com · port 5060");
    type("Provider address", "sips:pbx.example.com");
    screen.getByText("pbx.example.com · port 5061 · TLS");
    type("Provider address", "203.0.113.10:5070");
    screen.getByText("203.0.113.10 · port 5070");
    type("Provider address", "not a host!");
    screen.getByText(/doesn't look like a valid address/);
    // An Advanced choice changes what the address resolves to.
    type("Provider address", "pbx.example.com");
    fireEvent.click(screen.getByRole("button", { name: "Advanced settings" }));
    fireEvent.click(screen.getByRole("combobox", { name: "Transport" }));
    fireEvent.click(screen.getByRole("option", { name: "TCP" }));
    screen.getByText("pbx.example.com · port 5060 · TCP");
});

test("a provider that allows our server by IP address is saved with no credentials and no sign-in", async () => {
    const state = mount({ rows: [], write: async () => trunk({ id: "trunk-new" }) });
    await openConnectDialog();
    type("Provider address", "203.0.113.10");
    assert.ok(screen.getByLabelText("Username"));
    fireEvent.click(screen.getByRole("switch", { name: IP_ACCESS }));
    absent(screen.queryByLabelText("Username"));
    absent(screen.queryByLabelText("Password"));
    fireEvent.click(screen.getByRole("button", { name: "Save and test" }));
    await waitFor(() => assert.ok(state.writes.length >= 1));
    const body = state.writes[0].body as Record<string, unknown>;
    assert.equal("auth_username" in body, false);
    assert.equal("auth_password" in body, false);
    assert.equal(body.sip_domain, "203.0.113.10");
    assert.equal(body.port, 5060);
    assert.equal(body.transport, "udp");
    assert.equal((body.metadata as Record<string, unknown>).register, false);
});

test("Make calls and Receive calls choose the direction", async () => {
    const state = mount({ rows: [], write: async () => trunk({ id: "trunk-new" }) });
    await openConnectDialog();
    assert.equal(screen.getByRole("switch", { name: "Make calls" }).getAttribute("aria-checked"), "true");
    assert.equal(screen.getByRole("switch", { name: "Receive calls" }).getAttribute("aria-checked"), "true");
    type("Provider address", "pbx.example.com");
    type("Username", "alice");
    type("Password", "s3cret");

    fireEvent.click(screen.getByRole("switch", { name: "Make calls" }));
    fireEvent.click(screen.getByRole("switch", { name: "Receive calls" }));
    fireEvent.click(screen.getByRole("button", { name: "Save and test" }));
    assert.match((await screen.findByRole("alert")).textContent || "", /Make calls, Receive calls/);
    assert.equal(state.writes.length, 0);

    fireEvent.click(screen.getByRole("switch", { name: "Receive calls" }));
    fireEvent.click(screen.getByRole("button", { name: "Save and test" }));
    await waitFor(() => assert.ok(state.writes.length >= 1));
    assert.equal((state.writes[0].body as Record<string, unknown>).direction, "inbound");
});

test("missing details are explained in plain words and nothing is sent", async () => {
    const state = mount({ rows: [] });
    await openConnectDialog();
    fireEvent.click(screen.getByRole("button", { name: "Save and test" }));
    assert.match((await screen.findByRole("alert")).textContent || "", /Enter the address your provider gave you/);
    type("Provider address", "pbx.example.com");
    fireEvent.click(screen.getByRole("button", { name: "Save and test" }));
    await waitFor(() => assert.match(screen.getByRole("alert").textContent || "", /both username and password/));
    type("Username", "alice");
    type("Password", "s3cret");
    type("Number to show when calling", "call me");
    fireEvent.click(screen.getByRole("button", { name: "Save and test" }));
    await waitFor(() => assert.match(screen.getByRole("alert").textContent || "", /number to show/));
    assert.equal(state.writes.length, 0);
});

test("pressing Enter saves the line, and pressing it again while saving does not save twice", async () => {
    let release!: (value: unknown) => void;
    const gate = new Promise((resolve) => { release = resolve; });
    const state = mount({ rows: [], write: () => gate });
    await openConnectDialog();
    type("Provider address", "pbx.example.com");
    type("Username", "alice");
    type("Password", "s3cret");
    // Enter in a text box submits the form.
    const form = screen.getByLabelText("Provider address").closest("form") as HTMLFormElement;
    fireEvent.submit(form);
    await waitFor(() => assert.equal(state.writes.length, 1));
    fireEvent.submit(form);
    fireEvent.submit(form);
    release(trunk({ id: "trunk-new" }));
    await waitFor(() => assert.equal(screen.queryByRole("dialog") === null, true));
    // Settle: the new line is created once, then tested once. A second save would show up as another POST.
    await waitFor(() => assert.ok(state.writes.some((req) => req.path.endsWith("/test"))));
    assert.deepEqual(calls(state), ["POST /telephony/sip/trunks", "POST /telephony/sip/trunks/trunk-new/test"]);
});

test("when the server refuses a new line the dialog stays open and says why", async () => {
    const state = mount({ rows: [], write: async () => { throw problem(409, { title: "Duplicate", detail: "A line with this name already exists." }); } });
    await openConnectDialog();
    type("Provider address", "pbx.example.com");
    type("Username", "alice");
    type("Password", "s3cret");
    fireEvent.click(screen.getByRole("button", { name: "Save and test" }));
    await waitFor(() => assert.match(screen.getByRole("alert").textContent || "", /A line with this name already exists\./));
    assert.ok(screen.getByRole("dialog"));
    absent(screen.queryByText("Request failed"));
    // Nothing was saved, so there is nothing to test.
    assert.deepEqual(calls(state), ["POST /telephony/sip/trunks"]);
    assert.equal((screen.getByRole("button", { name: "Save and test" }) as HTMLButtonElement).disabled, false);
});

// ---------------------------------------------------------------------------------------------
// Edit
// ---------------------------------------------------------------------------------------------

test("editing a saved line preserves credentials without asking for its password, then tests it", async () => {
    const state = mount({ write: async (req) => (req.path.endsWith("/test") ? testResponse() : trunk()) });
    fireEvent.click(await screen.findByRole("button", { name: "Edit Test trunk" }));
    await screen.findByRole("dialog", { name: "Edit your PBX connection" });
    assert.equal((screen.getByLabelText("Provider address") as HTMLInputElement).value, "sip.example.com");
    assert.equal((screen.getByLabelText("Username") as HTMLInputElement).value, "saved-user");
    assert.equal((screen.getByLabelText("Password") as HTMLInputElement).value, "");
    type("Line name", "Renamed");
    fireEvent.click(screen.getByRole("button", { name: "Save and test" }));
    await waitFor(() => assert.equal(state.writes.length, 2));
    const [patch, probe] = state.writes;
    assert.equal(patch.method, "PATCH");
    assert.equal(patch.path, "/telephony/sip/trunks/trunk-1");
    const body = patch.body as Record<string, unknown>;
    assert.equal(body.trunk_name, "Renamed");
    assert.equal("auth_password" in body, false);
    assert.equal("auth_username" in body, false);
    assert.equal((body.metadata as Record<string, unknown>).unrelated, "keep");
    assert.equal(probe.path, "/telephony/sip/trunks/trunk-1/test");
    await waitFor(() => assert.ok(notified("Changes saved")));
});

test("switching to IP address access clears credentials and registration together", async () => {
    const state = mount({ write: async () => trunk() });
    fireEvent.click(await screen.findByRole("button", { name: "Edit Test trunk" }));
    fireEvent.click(await screen.findByRole("switch", { name: IP_ACCESS }));
    fireEvent.click(screen.getByRole("button", { name: "Save and test" }));
    await waitFor(() => assert.ok(state.writes.length >= 1));
    const body = state.writes[0].body as Record<string, unknown>;
    assert.equal(body.clear_auth, true);
    assert.equal((body.metadata as Record<string, unknown>).register, false);
    assert.equal("auth_password" in body, false);
});

test("changing username without a replacement password is blocked", async () => {
    const state = mount({ write: async () => trunk() });
    fireEvent.click(await screen.findByRole("button", { name: "Edit Test trunk" }));
    await screen.findByLabelText("Username");
    type("Username", "new-user");
    fireEvent.click(screen.getByRole("button", { name: "Save and test" }));
    assert.match(screen.getByRole("alert").textContent || "", /both username and password/);
    assert.equal(state.writes.length, 0);
});

test("saved settings the form does not show are kept when a line is edited", async () => {
    const state = mount({ rows: [trunk({ transport: "tcp", port: 5080, metadata: { register: true, srtp: true, outbound_proxy: "proxy.example.com:5060", unrelated: "keep" } })], write: async () => trunk() });
    fireEvent.click(await screen.findByRole("button", { name: "Edit Test trunk" }));
    assert.equal((await screen.findByLabelText("Provider address") as HTMLInputElement).value, "sip.example.com:5080");
    // Non-default Advanced values open the section so they are not hidden from the person editing.
    assert.equal((screen.getByLabelText("Outbound proxy") as HTMLInputElement).value, "proxy.example.com:5060");
    fireEvent.click(screen.getByRole("button", { name: "Save and test" }));
    await waitFor(() => assert.ok(state.writes.length >= 1));
    const body = state.writes[0].body as Record<string, unknown>;
    assert.equal(body.transport, "tcp");
    assert.equal(body.port, 5080);
    assert.deepEqual(body.metadata, { register: true, srtp: true, outbound_proxy: "proxy.example.com:5060", unrelated: "keep", register_interval: 3600, dtmf_mode: "rfc2833" });
});

// ---------------------------------------------------------------------------------------------
// Test: the real answer from the server, step by step
// ---------------------------------------------------------------------------------------------

test("Test shows the verdict first, then every check that ran", async () => {
    const answer = testResponse({
        ok: false, status: "credentials_rejected", sip_code: 403,
        verdict: "Your provider rejected the username or password.",
        steps: [
            { key: "address", ok: true, label: "Address found", detail: "sip.example.com points to 203.0.113.10." },
            { key: "reachability", ok: true, label: "Provider answered", detail: "Replied in 42 ms." },
            { key: "credentials", ok: false, label: "Sign-in", detail: "The provider answered 403 Forbidden.", sip_code: 403 },
            { key: "registration", ok: null, label: "Registration", detail: "Not attempted." },
        ],
    });
    const state = mount({ write: async () => answer });
    fireEvent.click(await screen.findByRole("button", { name: "Test Test trunk" }));
    const panel = await screen.findByRole("region", { name: "Test result for Test trunk" });
    assert.deepEqual(calls(state), ["POST /telephony/sip/trunks/trunk-1/test"]);
    assert.ok(state.writes[0].headers?.["Idempotency-Key"]);

    const verdict = within(panel).getByText("Your provider rejected the username or password.");
    const firstStep = within(panel).getByText("Address found");
    assert.ok(verdict.compareDocumentPosition(firstStep) & Node.DOCUMENT_POSITION_FOLLOWING, "the verdict comes before the steps");

    const steps = Array.from(panel.querySelectorAll("li[data-step]")).map((item) => ({ key: item.getAttribute("data-step") ?? "", text: item.textContent ?? "" }));
    assert.deepEqual(steps.map((step) => step.key), ["address", "reachability", "credentials", "registration"]);
    assert.match(steps[0].text, /Address found \(Passed\).*203\.0\.113\.10/);
    assert.match(steps[2].text, /Sign-in \(Failed\).*403 Forbidden.*SIP 403/);
    assert.match(steps[3].text, /Registration \(Skipped\).*Not attempted/);
    within(panel).getByText("Replied in 42 ms.");
});

test("while a test runs the button is busy, and it is free again afterwards", async () => {
    let release!: (value: unknown) => void;
    const gate = new Promise((resolve) => { release = resolve; });
    mount({ write: () => gate });
    const button = await screen.findByRole("button", { name: "Test Test trunk" }) as HTMLButtonElement;
    fireEvent.click(button);
    await screen.findByText(/Checking Test trunk/);
    assert.equal(button.disabled, true);
    within(button).getByText("Testing…");
    release(testResponse());
    await screen.findByText("Connected. Your provider accepted the sign-in.");
    assert.equal(button.disabled, false);
    absent(screen.queryByText(/Checking Test trunk/));
});

test("testing too often shows the server's message inline instead of failing", async () => {
    const state = mount({ write: async () => { throw problem(429, { title: "Too Many Requests", detail: "Wait 10 seconds before testing this line again." }); } });
    fireEvent.click(await screen.findByRole("button", { name: "Test Test trunk" }));
    const alert = await screen.findByRole("alert");
    assert.match(alert.textContent || "", /Wait 10 seconds before testing this line again\./);
    absent(screen.queryByText("Rate limited"));
    assert.equal((screen.getByRole("button", { name: "Test Test trunk" }) as HTMLButtonElement).disabled, false);
    assert.equal(state.writes.length, 1);
});

test("an older result with no steps or verdict still gets an honest verdict and one check", async () => {
    mount({ write: async () => ({ ok: false, detail: "No SIP response within 5s", tested_at: new Date().toISOString() }) });
    fireEvent.click(await screen.findByRole("button", { name: "Test Test trunk" }));
    const panel = await screen.findByRole("region", { name: "Test result for Test trunk" });
    within(panel).getByText("No SIP response within 5s", { selector: "p" });
    const steps = panel.querySelectorAll("li[data-step]");
    assert.equal(steps.length, 1);
    assert.match(steps[0].textContent || "", /Reaching your provider \(Failed\)/);
});

test("a test result can be dismissed", async () => {
    mount({ write: async () => testResponse() });
    fireEvent.click(await screen.findByRole("button", { name: "Test Test trunk" }));
    await screen.findByRole("region", { name: "Test result for Test trunk" });
    fireEvent.click(screen.getByRole("button", { name: "Dismiss test result for Test trunk" }));
    absent(screen.queryByRole("region", { name: "Test result for Test trunk" }));
});

// ---------------------------------------------------------------------------------------------
// On / Off
// ---------------------------------------------------------------------------------------------

test("the switch turns a line on and off with one call each", async () => {
    const state = mount({
        rows: [trunk({ is_active: false, live_registration_status: "inactive", runtime_status_code: "inactive" })],
        write: async (req, s) => {
            const on = req.path.endsWith("/activate");
            s.rows = [trunk({ is_active: on, live_registration_status: on ? "checking" : "inactive", runtime_status_code: on ? "runtime_checking" : "inactive" })];
            return s.rows[0];
        },
    });
    const name = "Use this line (Test trunk)";
    const toggle = async () => (await screen.findByRole("switch", { name }));
    assert.equal((await toggle()).getAttribute("aria-checked"), "false");

    fireEvent.click(await toggle());
    await waitFor(() => assert.equal(screen.getByRole("switch", { name }).getAttribute("aria-checked"), "true"));
    assert.deepEqual(calls(state), ["POST /telephony/sip/trunks/trunk-1/activate"]);
    assert.ok(state.writes[0].headers?.["Idempotency-Key"]);
    (await line("Test trunk")).getByText("Connecting");
    assert.ok(notified("Test trunk is on"));

    fireEvent.click(await toggle());
    await waitFor(() => assert.equal(screen.getByRole("switch", { name }).getAttribute("aria-checked"), "false"));
    assert.deepEqual(calls(state), ["POST /telephony/sip/trunks/trunk-1/activate", "POST /telephony/sip/trunks/trunk-1/deactivate"]);
    assert.ok(notified("Test trunk is off"));
});

// ---------------------------------------------------------------------------------------------
// Delete: one confirmation, one call
// ---------------------------------------------------------------------------------------------

test("delete asks once, removes the line in a single call, reports what changed and refreshes", async () => {
    const route = { ready: true, reason_code: "ready", caller_id: "+17789249977", trunk_id: "trunk-1", trunk_name: "Test trunk" };
    const state = mount({
        route,
        write: async (req, s) => {
            assert.ok(req.headers?.["Idempotency-Key"]);
            s.rows = [];
            return { id: "trunk-1", deleted: true, mode: "archived", archived_inbound_numbers: 2, unpinned_campaigns: 1, paused_inbound_campaigns: 1 };
        },
    });
    fireEvent.click(await screen.findByRole("button", { name: "Delete Test trunk" }));
    await screen.findByText("Delete Test trunk?");
    // One short plain warning: what stops, what is paused, what moves, what is kept.
    assert.equal(
        screen.getByText(/Calls stop using this line/).textContent,
        "Calls stop using this line, incoming numbers on it stop ringing, and their inbound campaigns are paused. Campaigns pinned to it switch to your default line, and call history is kept.",
    );
    assert.equal(state.writes.length, 0, "nothing is sent until the person confirms");

    const routeReads = () => state.gets.filter((path) => path.endsWith("/outbound-route")).length;
    const before = routeReads();
    fireEvent.click(screen.getByRole("button", { name: "Delete line" }));
    await waitFor(() => absent(screen.queryByRole("dialog")));
    // Directly, with no separate "turn it off first" step even though the line was on.
    assert.deepEqual(calls(state), ["DELETE /telephony/sip/trunks/trunk-1"]);
    await screen.findByText(/No PBX connected yet/);
    assert.ok(routeReads() > before, "the outbound summary is refreshed too");
    assert.equal(notified("Line deleted.")?.message, "Test trunk was removed. 2 incoming numbers disconnected. 1 inbound campaign paused.");
});

test("a deleted line with no incoming numbers says only that it was removed", async () => {
    mount({ write: async (_req, s) => { s.rows = []; return { id: "trunk-1", deleted: true, mode: "removed", archived_inbound_numbers: 0, unpinned_campaigns: 0, paused_inbound_campaigns: 0 }; } });
    fireEvent.click(await screen.findByRole("button", { name: "Delete Test trunk" }));
    fireEvent.click(await screen.findByRole("button", { name: "Delete line" }));
    await waitFor(() => assert.ok(notified("Line deleted.")));
    assert.equal(notified("Line deleted.")?.message, "Test trunk was removed.");
});

test("disconnected numbers and paused inbound campaigns are counted in the singular and the plural", async () => {
    mount({ write: async (_req, s) => { s.rows = []; return { id: "trunk-1", deleted: true, mode: "archived", archived_inbound_numbers: 1, paused_inbound_campaigns: 2 }; } });
    fireEvent.click(await screen.findByRole("button", { name: "Delete Test trunk" }));
    fireEvent.click(await screen.findByRole("button", { name: "Delete line" }));
    await waitFor(() => assert.ok(notified("Line deleted.")));
    assert.equal(notified("Line deleted.")?.message, "Test trunk was removed. 1 incoming number disconnected. 2 inbound campaigns paused.");
});

test("a paused inbound campaign is reported even when no number count came back", async () => {
    mount({ write: async (_req, s) => { s.rows = []; return { id: "trunk-1", deleted: true, mode: "archived", paused_inbound_campaigns: 1 }; } });
    fireEvent.click(await screen.findByRole("button", { name: "Delete Test trunk" }));
    fireEvent.click(await screen.findByRole("button", { name: "Delete line" }));
    await waitFor(() => assert.ok(notified("Line deleted.")));
    assert.equal(notified("Line deleted.")?.message, "Test trunk was removed. 1 inbound campaign paused.");
});

test("a refused delete keeps the dialog open with the server's own explanation", async () => {
    const state = mount({ write: async () => { throw problem(409, { title: "Trunk In Use", detail: "Finish the call in progress on this line, then try again." }); } });
    fireEvent.click(await screen.findByRole("button", { name: "Delete Test trunk" }));
    fireEvent.click(await screen.findByRole("button", { name: "Delete line" }));
    await screen.findByText("Finish the call in progress on this line, then try again.");
    assert.ok(screen.getByRole("dialog"));
    absent(screen.queryByText("Request failed"));
    assert.deepEqual(calls(state), ["DELETE /telephony/sip/trunks/trunk-1"]);
    assert.ok(screen.getByRole("heading", { name: "Test trunk" }), "the line is still listed");
});

test("any other delete failure also stays in the dialog", async () => {
    const state = mount({ write: async () => { throw new Error("A campaign still uses this trunk. Remove its assignment first."); } });
    fireEvent.click(await screen.findByRole("button", { name: "Delete Test trunk" }));
    fireEvent.click(await screen.findByRole("button", { name: "Delete line" }));
    await screen.findByText("A campaign still uses this trunk. Remove its assignment first.");
    assert.ok(screen.getByRole("dialog"));
    assert.deepEqual(calls(state), ["DELETE /telephony/sip/trunks/trunk-1"]);
});
