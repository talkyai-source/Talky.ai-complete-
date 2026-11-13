/**
 * FX2a2 Task 2.3 — Billing overview and the invoice list announce their
 * loading through the shared LoadingState, with their existing loading texts
 * as its title.
 */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, screen, within } from "@testing-library/react";
import type { QueryClient } from "@tanstack/react-query";

import { BillingOverview } from "@/components/billing/billing-overview";
import { BillingInvoiceList } from "@/components/billing/invoice-view";
import { renderWithQueryClient } from "@/test-utils/render";

const originalFetch = globalThis.fetch;
const clients: QueryClient[] = [];
afterEach(() => { cleanup(); globalThis.fetch = originalFetch; clients.splice(0).forEach((client) => client.clear()); });

// Never answers: the read stays in flight.
function pending(ui: React.ReactElement) {
    globalThis.fetch = (() => new Promise<Response>(() => {})) as unknown as typeof fetch;
    clients.push(renderWithQueryClient(ui).qc);
}

test("Billing overview loading is the shared LoadingState titled 'Loading billing data…'", async () => {
    pending(<BillingOverview />);
    const status = await screen.findByRole("status");
    assert.equal(within(status).queryByRole("heading", { name: "Loading billing data…" }) !== null, true);
    assert.equal(screen.queryByText("Billing data did not load") === null, true);
});

test("Invoice list loading is the shared LoadingState titled 'Loading invoices…'", async () => {
    pending(<BillingInvoiceList />);
    const status = await screen.findByRole("status");
    assert.equal(within(status).queryByRole("heading", { name: "Loading invoices…" }) !== null, true);
    assert.equal(screen.queryByText("No invoices recorded.") === null, true);
});
