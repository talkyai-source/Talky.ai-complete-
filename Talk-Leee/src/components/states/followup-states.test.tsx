/**
 * FX2b2 — follow-ups: a failed passkey delete is an inline alert (no Retry,
 * the list stays); the remaining own-markup areas on the P1 pages use the
 * shared LoadingState / ErrorState / EmptyState with their existing texts.
 */
import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import DeviceList from "@/components/auth/device-list";
import PasskeyList from "@/components/auth/passkey-list";
import { BillingOverview } from "@/components/billing/billing-overview";
import { SummaryPreview } from "@/components/calls/call-panels";
import { DialerInsights } from "@/components/dashboard/dialer-insights";
import { InboundPermissionState } from "@/components/inbound/inbound-page-state";
import { TelephonyProvidersSection } from "@/components/settings/telephony-providers-section";
import { api } from "@/lib/api";
import { extendedApi } from "@/lib/extended-api";
import { notificationsStore } from "@/lib/notifications";
import { renderWithQueryClient } from "@/test-utils/render";

const SLOW = 15_000;
const realRequest = api.request;
const realFetch = globalThis.fetch;
const clients: QueryClient[] = [];
afterEach(() => {
    cleanup();
    mock.restoreAll();
    clients.splice(0).forEach((client) => client.clear());
    api.request = realRequest;
    globalThis.fetch = realFetch;
    notificationsStore.setIdentity(null);
});
const shown = (text: string | RegExp) => screen.queryByText(text) !== null;
// The shared LoadingState: a status whose title is a heading.
const loadingTitled = (title: string) =>
    screen.queryAllByRole("status").some((node) => within(node).queryByRole("heading", { name: title }) !== null);
const emptyTitle = async (text: string) => {
    const title = await screen.findByText(text, undefined, { timeout: SLOW });
    assert.equal(title.tagName, "H2", "the shared EmptyState title");
};

// ───────── Task 1: passkey delete ─────────

type Passkeys = Awaited<ReturnType<typeof api.listPasskeys>>;
const passkey = {
    id: "pk-1", credential_id: "cred", display_name: "Laptop key", device_type: "platform", backed_up: false,
    transports: [], created_at: "2026-01-01T00:00:00Z",
};

test("a failed passkey delete shows inline as an alert with its text, keeps the list and offers no Retry", async () => {
    const read = mock.method(api, "listPasskeys", async () => [passkey] as Passkeys);
    mock.method(api, "deletePasskey", async () => { throw new Error("Passkey could not be removed."); });
    render(<PasskeyList />);
    fireEvent.click(await screen.findByRole("button", { name: "Remove passkey Laptop key" }, { timeout: SLOW }));
    const dialog = within(await screen.findByRole("dialog"));
    fireEvent.click(dialog.getByRole("button", { name: "Remove passkey Laptop key" }));
    const message = await screen.findByText("Passkey could not be removed.", undefined, { timeout: SLOW });
    assert.equal(message.getAttribute("role"), "alert");
    assert.equal(shown("Laptop key"), true, "the list stays");
    assert.equal(screen.queryByRole("button", { name: "Retry" }) === null, true, "a failed delete is not a failed read");
    assert.equal(shown("Something went wrong"), false);
    assert.equal(read.mock.callCount(), 1);
});

test("no passkeys shows 'No passkeys registered yet.' in the shared EmptyState", async () => {
    mock.method(api, "listPasskeys", async () => [] as Passkeys);
    render(<PasskeyList />);
    await emptyTitle("No passkeys registered yet.");
});

// ───────── Task 3.1: summary hover preview ─────────

test("summary preview: loading is the shared LoadingState titled 'Loading key points…'", () => {
    render(<SummaryPreview isLoading isError={false} />);
    assert.equal(loadingTitled("Loading key points…"), true);
});

test("summary preview: a failed read shows 'Could not load the AI summary.' in the shared ErrorState, with no Retry inside the hover preview", () => {
    // Even a caller that still passes a retry gets no button here.
    const withRetry = { onRetry: () => {} } as object;
    render(<SummaryPreview isLoading={false} isError {...withRetry} />);
    assert.equal(screen.getByText("Could not load the AI summary.").getAttribute("role"), "alert");
    assert.equal(shown("Something went wrong"), true, "the shared ErrorState");
    // A control inside a hover preview cannot be reached by keyboard.
    assert.equal(screen.queryByRole("button", { name: "Retry" }) === null, true);
});

// ───────── Task 3.2: devices ─────────

test("devices: loading is the shared LoadingState (default title)", async () => {
    type Sessions = Awaited<ReturnType<typeof api.getActiveSessions>>;
    let answer: (() => void) | null = null;
    mock.method(api, "getActiveSessions", () => new Promise<Sessions>((resolve) => { answer = () => resolve({ sessions: [], total: 0 }); }));
    render(<DeviceList token="" />);
    assert.equal(loadingTitled("Loading"), true);
    await waitFor(() => assert.equal(answer !== null, true), { timeout: SLOW });
    await act(async () => { answer!(); });
    await waitFor(() => assert.equal(shown("No active devices"), true), { timeout: SLOW });
});

// ───────── Task 3.3: PBX list ─────────

type Options = Parameters<typeof api.request>[0];
function mountTelephony(trunks: () => Promise<unknown>) {
    const counts = { trunks: 0 };
    notificationsStore.setIdentity({ tenantId: "followup-ui", userId: "owner" });
    api.request = async <T,>(options: Options) => {
        if (options.path === "/telephony/providers") return { active: "none", availability: {}, providers: [] } as T;
        if (options.path === "/telephony/sip/trunks") { counts.trunks += 1; return await trunks() as T; }
        if (options.path.endsWith("/pool-assignment")) return {} as T;
        if (options.path.endsWith("/outbound-route")) return { ready: true, reason_code: "sip_not_required" } as T;
        return [] as T;
    };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false, gcTime: Infinity } } });
    clients.push(client);
    render(<QueryClientProvider client={client}><TelephonyProvidersSection /></QueryClientProvider>);
    return counts;
}

test("PBX list: loading is the shared LoadingState titled 'Loading your PBX connections…'", async () => {
    mountTelephony(() => new Promise(() => {}));
    await waitFor(() => assert.equal(loadingTitled("Loading your PBX connections…"), true), { timeout: SLOW });
});

test("PBX list: a failed read shows 'Could not load your PBX connections.' in the shared ErrorState with Retry, which reads again", async () => {
    let fail = true;
    const counts = mountTelephony(async () => { if (fail) throw new Error("network down"); return []; });
    const sentence = await screen.findByText("Could not load your PBX connections.", undefined, { timeout: SLOW });
    assert.equal(sentence.getAttribute("role"), "alert");
    assert.equal(shown("Something went wrong"), true, "the shared ErrorState");
    fail = false;
    const before = counts.trunks;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => assert.equal(shown("No PBX connected yet."), true), { timeout: SLOW });
    assert.equal(counts.trunks, before + 1);
});

// ───────── Task 3.5: inbound permission refusal ─────────

test("inbound permission refusal: the shared ErrorState with its existing sentences and Retry, which checks again", () => {
    let checks = 0;
    render(<InboundPermissionState onRetry={() => { checks += 1; }} />);
    assert.equal(screen.getByText("You cannot view inbound campaigns").tagName, "H2");
    assert.equal(
        screen.getByText("Ask a tenant administrator to grant the specific inbound capability. Access is re-checked by the server for every action.").getAttribute("role"),
        "alert",
    );
    assert.equal(shown("Try these quick checks"), true, "the shared ErrorState");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    assert.equal(checks, 1);
});

// ───────── Task 3.6: billing overview empties ─────────

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
function mountEmptyBilling() {
    globalThis.fetch = (async (input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
        const path = new URL(url).pathname.replace(/^\/api\/v1/, "");
        if (path === "/billing/subscription") return json({
            status: "inactive", plan_id: null, plan_name: null, current_period_start: null, current_period_end: null,
            cancel_at_period_end: false, minutes_allocated: 0, minutes_used: 0, minutes_remaining: 0,
        });
        if (path === "/billing/topups/balance") return json({
            allocated: 0, used_minutes: 0, remaining_minutes: 0, unlimited: false, exhausted: false, purchased_minutes: 0, topup_minutes_left: 0,
        });
        if (path === "/billing/usage") return json({ usage_type: "minutes", total_used: 0, allocated: 0, remaining: 0, overage: 0, unlimited: true, metering_period: "calendar_month" });
        return json([]);
    }) as typeof fetch;
    clients.push(renderWithQueryClient(<BillingOverview />).qc);
}

test("billing overview: 'No invoices yet.' is the shared EmptyState", async () => {
    mountEmptyBilling();
    await emptyTitle("No invoices yet.");
});

test("billing overview: 'No call activity in the last 30 days.' is the shared EmptyState", async () => {
    mountEmptyBilling();
    await emptyTitle("No call activity in the last 30 days.");
});

// ───────── Task 3.7: dialer insights ─────────

test("dialer insights: 'No attempts in range.' is the shared EmptyState", async () => {
    mock.method(extendedApi, "getBestTimeToCall", async () => ({ timezone: "UTC", best_hour: null, hours: [] }));
    mock.method(extendedApi, "getRetryEffectiveness", async () => ({ attempts: [] }));
    render(<DialerInsights />);
    await emptyTitle("No attempts in range.");
});
