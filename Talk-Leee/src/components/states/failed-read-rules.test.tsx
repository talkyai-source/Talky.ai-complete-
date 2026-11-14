/**
 * FX2b3 — the failed-read rules: a JavaScript error or a validation failure
 * shows "Please retry." (only a server's own message is shown as is); the PBX
 * card keeps its error while a poll runs; an unexpected pool, outbound-route
 * or PBX-list answer is a failed read with Retry, never a crash or an empty
 * list.
 */
import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { z } from "zod";

import DeviceList from "@/components/auth/device-list";
import { DialerInsights } from "@/components/dashboard/dialer-insights";
import { RecordingPolicySection } from "@/components/settings/recording-policy-section";
import { TelephonyProvidersSection } from "@/components/settings/telephony-providers-section";
import { ERROR_STATE_DEFAULT_MESSAGE, failedReadMessage } from "@/components/states/page-states";
import { api } from "@/lib/api";
import { extendedApi } from "@/lib/extended-api";
import { setRequestIdentity, setTokenProvider } from "@/lib/http-client";
import { notificationsStore } from "@/lib/notifications";

const SLOW = 15_000;
const RAW = /Cannot read properties|is not a function|is not defined|invalid_type/i;
const realRequest = api.request;
const realFetch = globalThis.fetch;
const clients: QueryClient[] = [];
afterEach(() => {
    cleanup();
    mock.restoreAll();
    clients.splice(0).forEach((client) => client.clear());
    api.request = realRequest;
    globalThis.fetch = realFetch;
    setTokenProvider(null);
    notificationsStore.setIdentity(null);
});
const shown = (text: string | RegExp) => screen.queryAllByText(text).length > 0;
// What reading a missing list really throws.
function jsError(): TypeError {
    try {
        (undefined as unknown as { map: (fn: () => void) => void }).map(() => {});
    } catch (err) {
        return err as TypeError;
    }
    throw new Error("expected a TypeError");
}

// ───────── Task 2: the message rule ─────────

test("failedReadMessage: a JavaScript error or a validation failure gives 'Please retry.'; a server message is kept", () => {
    assert.equal(ERROR_STATE_DEFAULT_MESSAGE, "Please retry.");
    assert.match(jsError().message, /Cannot read properties/);
    assert.equal(failedReadMessage(jsError(), "Failed to load calls"), "Please retry.");
    assert.equal(failedReadMessage(new ReferenceError("x is not defined")), "Please retry.");
    assert.equal(failedReadMessage(new SyntaxError("Unexpected token <")), "Please retry.");
    const parsed = z.object({ items: z.array(z.string()) }).safeParse({ items: 1 });
    assert.equal(parsed.success, false);
    assert.equal(failedReadMessage(parsed.error), "Please retry.");
    assert.equal(failedReadMessage(new Error("Recording policy store is unavailable.")), "Recording policy store is unavailable.");
    assert.equal(failedReadMessage(new Error(""), "Failed to load calls"), "Failed to load calls");
});

test("devices: a JavaScript error shows 'Please retry.' with Retry, never the raw text", async () => {
    mock.method(api, "getActiveSessions", async () => { throw jsError(); });
    render(<DeviceList token="" />);
    await waitFor(() => assert.equal(shown("Please retry."), true), { timeout: SLOW });
    assert.equal(shown(RAW), false);
    assert.equal(screen.queryByRole("button", { name: "Retry" }) !== null, true);
});

test("dialer insights: a JavaScript error shows 'Please retry.' with Retry, never the raw text", async () => {
    mock.method(extendedApi, "getBestTimeToCall", async () => { throw jsError(); });
    mock.method(extendedApi, "getRetryEffectiveness", async () => ({ attempts: [] }));
    render(<DialerInsights />);
    await waitFor(() => assert.equal(shown("Please retry."), true), { timeout: SLOW });
    assert.equal(shown(RAW), false);
});

test("recording policy: a read that fails with a JavaScript error shows 'Please retry.' with Retry, never the raw text", async () => {
    setTokenProvider(() => null);
    setRequestIdentity({ userId: "user-a", tenantId: "tenant-a" });
    globalThis.fetch = (async () => { throw jsError(); }) as typeof fetch;
    render(<RecordingPolicySection canEdit />);
    await waitFor(() => assert.equal(screen.queryByRole("button", { name: "Retry" }) !== null, true), { timeout: SLOW });
    assert.equal(shown(RAW), false);
});

// ───────── Tasks 1 and 3.1: the PBX card ─────────

type Options = Parameters<typeof api.request>[0];
type Reads = { trunks?: () => Promise<unknown>; pool?: () => Promise<unknown>; route?: () => Promise<unknown> };
const goodRoute = { ready: false, reason_code: "no_trunk", reason: null };
function mountTelephony(reads: Reads) {
    const counts = { trunks: 0, pool: 0, route: 0 };
    notificationsStore.setIdentity({ tenantId: "rules-ui", userId: "owner" });
    api.request = async <T,>(options: Options) => {
        if (options.path === "/telephony/providers") return { active: "none", availability: {}, providers: [] } as T;
        if (options.path === "/telephony/sip/trunks") { counts.trunks += 1; return await (reads.trunks ? reads.trunks() : []) as T; }
        if (options.path === "/telephony/sip/trunks/pool") { counts.pool += 1; return await (reads.pool ? reads.pool() : []) as T; }
        if (options.path === "/telephony/sip/trunks/outbound-route") { counts.route += 1; return await (reads.route ? reads.route() : goodRoute) as T; }
        if (options.path.endsWith("/pool-assignment")) return {} as T;
        return [] as T;
    };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false, gcTime: Infinity } } });
    clients.push(client);
    render(<QueryClientProvider client={client}><TelephonyProvidersSection /></QueryClientProvider>);
    return { counts, client };
}
const PBX_ERROR = "Could not load your PBX connections.";
const PBX_LOADING = "Loading your PBX connections…";

test("PBX list: while a poll runs after a failed read, the error and its Retry stay; the loading state does not come back", async () => {
    let poll: "fail" | "hang" = "fail";
    const { counts, client } = mountTelephony({ trunks: () => (poll === "fail" ? Promise.reject(new Error("network down")) : new Promise(() => {})) });
    await screen.findByText(PBX_ERROR, undefined, { timeout: SLOW });
    poll = "hang";
    const before = counts.trunks;
    // What the 10-second poll does: read again.
    await act(async () => { void client.refetchQueries({ queryKey: ["telephony"] }); await new Promise((resolve) => setTimeout(resolve, 50)); });
    await waitFor(() => assert.equal(counts.trunks > before, true), { timeout: SLOW });
    assert.equal(shown(PBX_ERROR), true, "the error stays while the poll runs");
    assert.equal(screen.queryByRole("button", { name: "Retry" }) !== null, true);
    assert.equal(shown(PBX_LOADING), false);
});

test("PBX list: the first load still shows the LoadingState", async () => {
    mountTelephony({ trunks: () => new Promise(() => {}) });
    await waitFor(() => assert.equal(
        screen.queryAllByRole("status").some((node) => within(node).queryByRole("heading", { name: PBX_LOADING }) !== null), true,
    ), { timeout: SLOW });
});

test("PBX list: an answer that holds no list is a failed read, never 'No PBX connected yet.'", async () => {
    mountTelephony({ trunks: async () => ({}) });
    await screen.findByText(PBX_ERROR, undefined, { timeout: SLOW });
    assert.equal(shown("No PBX connected yet."), false);
});

test("pool read answering {} shows the ErrorState with Retry, which reads again; the page does not crash", async () => {
    let answer: unknown = {};
    const { counts } = mountTelephony({ pool: async () => answer });
    await screen.findByText("Your PBX", undefined, { timeout: SLOW });
    await waitFor(() => assert.equal(shown("Please retry."), true), { timeout: SLOW });
    assert.equal(shown(RAW), false);
    assert.equal(shown("No PBX connected yet."), true, "the rest of the card still shows");
    answer = [{ id: "pool-1", label: "Pool 150001", runtime_ready: true, runtime_status_detail: "" }];
    const before = counts.pool;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => assert.equal(shown("Shared trunk account"), true), { timeout: SLOW });
    assert.equal(counts.pool, before + 1);
    assert.equal(shown("Please retry."), false);
});

test("outbound-route read answering {} shows the ErrorState with Retry, which reads again; never silence", async () => {
    let answer: unknown = {};
    const { counts } = mountTelephony({ route: async () => answer });
    await screen.findByText("Your PBX", undefined, { timeout: SLOW });
    await waitFor(() => assert.equal(shown("Please retry."), true), { timeout: SLOW });
    answer = goodRoute;
    const before = counts.route;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => assert.equal(shown(/Outbound calls can.t go out right now/), true), { timeout: SLOW });
    assert.equal(counts.route, before + 1);
    assert.equal(shown("Please retry."), false);
});

// ── FX3a2 Task 5 ─────────────────────────────────────────────────────────────
test("FX3a2: the app's own time limit is not a server message", () => {
    const timedOut = Object.assign(new Error("Request timed out"), { code: "timeout" });
    assert.equal(failedReadMessage(timedOut, "Request failed"), ERROR_STATE_DEFAULT_MESSAGE);
    assert.equal(failedReadMessage(timedOut, "Request failed") === "Request timed out", false);
    const server = Object.assign(new Error("Connector service is unavailable"), { code: "server_error" });
    assert.equal(failedReadMessage(server, "Request failed"), "Connector service is unavailable");
});
