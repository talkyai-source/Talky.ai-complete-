import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api } from "@/lib/api";
import type { SipTrunkRow } from "@/lib/telephony-api";
import { SipTrunksList } from "./sip-trunks-list";

const request = api.request;
const clients: QueryClient[] = [];
// jsdom has no layout engine; the shared Select scrolls its active option.
if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};
afterEach(() => { cleanup(); clients.splice(0).forEach((client) => client.clear()); api.request = request; });

function trunk(overrides: Partial<SipTrunkRow> = {}): SipTrunkRow {
    return { id: "trunk-1", tenant_id: "tenant-1", trunk_name: "Test trunk", sip_domain: "sip.example.com", port: 5060, transport: "udp", direction: "both", is_active: true, auth_configured: true, auth_username: "saved-user", metadata: { register: true, caller_id: "+442079460000", unrelated: "keep" }, runtime_ready: false, runtime_status_code: "unreachable", runtime_status_detail: "No SIP response within 5s", live_registration_status: "registered", created_at: "2026-10-02T00:00:00Z", ...overrides };
}

function mount(row = trunk(), mutate: (options: Parameters<typeof api.request>[0]) => Promise<unknown> = async () => row, route: unknown = {}) {
    api.request = async <T,>(options: Parameters<typeof api.request>[0]) => {
        if (options.method && options.method !== "GET") return await mutate(options) as T;
        if (options.path.endsWith("/outbound-route")) return route as T;
        if (options.path.endsWith("/pool")) return [] as T;
        if (options.path.endsWith("/pool-assignment")) return {} as T;
        return [row] as T;
    };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false, gcTime: 0 } } });
    clients.push(client);
    render(<QueryClientProvider client={client}><SipTrunksList /></QueryClientProvider>);
}

test("registration and a successful old probe do not claim calling readiness", async () => {
    mount(trunk({ last_test_result: { ok: true, detail: "SIP 200 OK" }, last_tested_at: "2026-09-01T00:00:00Z" }));
    await screen.findByText("Outbound not ready · No SIP response within 5s");
    assert.equal(screen.queryByText(/^Outbound ready ·/), null);
    assert.ok(screen.getByText("Last probe: SIP 200 OK"));
    assert.ok(screen.getByRole("button", { name: "Disable Test trunk" }));
});

test("editing a saved trunk preserves credentials without asking for its password", async () => {
    const writes: Parameters<typeof api.request>[0][] = [];
    mount(trunk(), async (options) => { writes.push(options); return trunk(); });
    fireEvent.click(await screen.findByRole("button", { name: "Edit Test trunk" }));
    fireEvent.change(screen.getByLabelText("Trunk name"), { target: { value: "Renamed" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => assert.equal(writes.length, 1));
    const body = writes[0].body as Record<string, unknown>;
    assert.equal(body.trunk_name, "Renamed");
    assert.equal("auth_password" in body, false);
    assert.equal("auth_username" in body, false);
    assert.equal((body.metadata as Record<string, unknown>).unrelated, "keep");
});

test("switching to IP authentication clears credentials and registration together", async () => {
    const writes: Parameters<typeof api.request>[0][] = [];
    mount(trunk(), async (options) => { writes.push(options); return trunk(); });
    fireEvent.click(await screen.findByRole("button", { name: "Edit Test trunk" }));
    fireEvent.click(screen.getByRole("combobox", { name: "Authentication" }));
    fireEvent.click(screen.getByRole("option", { name: "IP allowlist (no password)" }));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => assert.equal(writes.length, 1));
    const body = writes[0].body as Record<string, unknown>;
    assert.equal(body.clear_auth, true);
    assert.equal((body.metadata as Record<string, unknown>).register, false);
    assert.equal("auth_password" in body, false);
});

test("changing username without a replacement password is blocked", async () => {
    let writes = 0;
    mount(trunk(), async () => { writes++; return trunk(); });
    fireEvent.click(await screen.findByRole("button", { name: "Edit Test trunk" }));
    fireEvent.change(screen.getByLabelText("Auth username"), { target: { value: "new-user" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    assert.match(screen.getByRole("alert").textContent || "", /both username and password/);
    assert.equal(writes, 0);
});

test("delete explicitly disables first and retains the server conflict in the dialog", async () => {
    const paths: string[] = [];
    mount(trunk(), async (options) => {
        paths.push(options.path);
        assert.ok(options.headers?.["Idempotency-Key"]);
        if (options.method === "DELETE") throw new Error("A campaign still uses this trunk. Remove its assignment first.");
        return trunk({ is_active: false });
    });
    fireEvent.click(await screen.findByRole("button", { name: "Delete Test trunk" }));
    assert.equal(paths.length, 0);
    fireEvent.click(screen.getByRole("button", { name: "Disable and delete" }));
    await screen.findByText("A campaign still uses this trunk. Remove its assignment first.");
    assert.deepEqual(paths, ["/telephony/sip/trunks/trunk-1/deactivate", "/telephony/sip/trunks/trunk-1"]);
    assert.ok(screen.getByRole("dialog"));
});

test("IP trunk can be ready without registration or credentials", async () => {
    mount(trunk({ auth_configured: false, auth_username: null, metadata: { register: false }, live_registration_status: "reachable", runtime_ready: true, runtime_status_detail: "Outbound contact available" }));
    await screen.findByText("Outbound ready · Outbound contact available");
    assert.ok(screen.getByText("IP allowlist"));
});

test("an inbound-only IP trunk uses inbound proof despite an unanswered outbound probe", async () => {
    mount(trunk({ direction: "inbound", auth_configured: false, auth_username: null, runtime_ready: false, inbound_runtime_ready: true, live_registration_status: "unreachable" }));
    await screen.findByText("Inbound ready · Inbound configuration is ready.");
    assert.equal(screen.queryByText(/^Outbound ready/), null);
});


test("the page shows the number outbound calls really go out from, not only a stored caller ID", async () => {
    // Owner report 2026-10-14: the backend dialled one number while this page
    // kept showing another (the trunk's stored caller ID).
    mount(trunk(), undefined, { ready: true, reason_code: "ready", caller_id: "+17789249977", trunk_id: "trunk-1", trunk_name: "Test trunk" });
    const status = await screen.findByText(/Outbound calls go out from/);
    assert.match(status.textContent || "", /\+17789249977 on Test trunk/);
    assert.ok(screen.getByText("Outbound calls use this line"));
    assert.ok(screen.getByText("Caller ID: +442079460000"));
});

test("a line the dialer cannot use says so instead of showing a number as if it worked", async () => {
    mount(trunk(), undefined, { ready: false, reason_code: "selected_trunk_not_ready", reason: "Selected SIP trunk is not ready (selected_trunk_not_ready). Check its live status in Settings.", caller_id: "+442046132300", trunk_id: "trunk-1" });
    const status = await screen.findByText(/Outbound calls can.t go out right now/);
    assert.match(status.textContent || "", /not ready/);
    assert.match(status.textContent || "", /\+442046132300/);
    assert.equal(screen.queryByText("Outbound calls use this line"), null);
});

test("an account that does not dial through SIP lines shows no outbound line summary", async () => {
    mount(trunk(), undefined, { ready: true, reason_code: "sip_not_required", caller_id: "1001", trunk_id: null });
    await screen.findByText("Test trunk");
    assert.equal(screen.queryByText(/Outbound calls go out from/), null);
    assert.equal(screen.queryByText(/1001/), null);
});
