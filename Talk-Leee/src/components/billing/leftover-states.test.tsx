/**
 * FX2b Task 8 — the Billing leftovers use the shared states with their
 * existing texts: "Loading call stats…", "Loading daily usage…" and
 * "Loading invoice…" are the shared LoadingState; "No invoices recorded." is
 * the shared EmptyState.
 */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import type { QueryClient } from "@tanstack/react-query";

import { BillingOverview } from "@/components/billing/billing-overview";
import { BillingInvoiceDetail, BillingInvoiceList } from "@/components/billing/invoice-view";
import { renderWithQueryClient } from "@/test-utils/render";

const SLOW = 15_000;
const originalFetch = globalThis.fetch;
const clients: QueryClient[] = [];
afterEach(() => { cleanup(); globalThis.fetch = originalFetch; clients.splice(0).forEach((client) => client.clear()); });
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
const show = (ui: React.ReactElement) => { clients.push(renderWithQueryClient(ui).qc); };
const loadingTitled = (title: string) =>
    screen.queryAllByRole("status").some((node) => within(node).queryByRole("heading", { name: title }) !== null);

const SUBSCRIPTION = {
    status: "inactive", plan_id: null, plan_name: null, current_period_start: null, current_period_end: null,
    cancel_at_period_end: false, minutes_allocated: 0, minutes_used: 0, minutes_remaining: 0,
};
const USAGE = { usage_type: "minutes", total_used: 0, allocated: 0, remaining: 0, overage: 0, unlimited: true, metering_period: "calendar_month" };
const BALANCE = {
    allocated: 0, used_minutes: 0, remaining_minutes: 0, unlimited: false, exhausted: false, purchased_minutes: 0, topup_minutes_left: 0,
};

// Every billing read answers except the daily usage read, which stays in flight.
function overviewWithDailyPending() {
    globalThis.fetch = (async (input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
        const path = new URL(url).pathname.replace(/^\/api\/v1/, "");
        if (path === "/billing/usage/daily") return new Promise<Response>(() => {});
        if (path === "/billing/subscription") return json(SUBSCRIPTION);
        if (path === "/billing/topups/balance") return json(BALANCE);
        if (path === "/billing/usage") return json(USAGE);
        return json([]);
    }) as typeof fetch;
    show(<BillingOverview />);
}

test("Call stats loading is the shared LoadingState titled 'Loading call stats…'", async () => {
    overviewWithDailyPending();
    await waitFor(() => assert.equal(loadingTitled("Loading call stats…"), true), { timeout: SLOW });
});

test("Daily usage loading is the shared LoadingState titled 'Loading daily usage…'", async () => {
    overviewWithDailyPending();
    await waitFor(() => assert.equal(loadingTitled("Loading daily usage…"), true), { timeout: SLOW });
});

test("Single invoice loading is the shared LoadingState titled 'Loading invoice…'", async () => {
    globalThis.fetch = (() => new Promise<Response>(() => {})) as unknown as typeof fetch;
    show(<BillingInvoiceDetail id="inv-1" />);
    await waitFor(() => assert.equal(loadingTitled("Loading invoice…"), true), { timeout: SLOW });
});

test("An empty invoice list shows 'No invoices recorded.' in the shared EmptyState", async () => {
    globalThis.fetch = (async () => json([])) as typeof fetch;
    show(<BillingInvoiceList />);
    const title = await screen.findByText("No invoices recorded.", undefined, { timeout: SLOW });
    assert.equal(title.tagName, "H2", "the shared EmptyState title");
});
