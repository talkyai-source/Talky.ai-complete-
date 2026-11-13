/** FX3a Task 5.4 — an invoice link on the Billing overview reads the invoice number, as the Invoices page does. */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { cleanup, screen } from "@testing-library/react";
import { BillingOverview } from "@/components/billing/billing-overview";
import { invoiceFixture } from "@/test-utils/billing-fixtures";
import { renderWithQueryClient } from "@/test-utils/render";

const originalFetch = globalThis.fetch;
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });

const SUBSCRIPTION = {
    status: "inactive", plan_id: null, plan_name: null, current_period_start: null, current_period_end: null,
    cancel_at_period_end: false, minutes_allocated: 0, minutes_used: 0, minutes_remaining: 0,
};
const USAGE = { usage_type: "minutes", total_used: 0, allocated: 0, remaining: 0, overage: 0, unlimited: true, metering_period: "calendar_month" };
const BALANCE = { allocated: 0, used_minutes: 0, remaining_minutes: 0, unlimited: false, exhausted: false, purchased_minutes: 0, topup_minutes_left: 0 };

function routes(invoices: unknown[]): Record<string, () => Response> {
    return {
        "/billing/subscription": () => json(SUBSCRIPTION),
        "/billing/topups/balance": () => json(BALANCE),
        "/billing/usage": () => json(USAGE),
        "/billing/usage/daily": () => json([]),
        "/billing/invoices": () => json(invoices),
        "/billing/overage-alerts": () => json([]),
        "/billing/adjustments": () => json([]),
    };
}

function mockFetch(table: Record<string, () => Response>) {
    globalThis.fetch = (async (input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
        const path = new URL(url).pathname.replace(/^\/api\/v1/, "");
        const handler = table[path];
        if (!handler) throw new Error(`unrouted billing path in test: ${path}`);
        return handler();
    }) as typeof fetch;
}

afterEach(() => { cleanup(); globalThis.fetch = originalFetch; });

test("FX3a: the invoice link reads the invoice number, not the payment provider's id", async () => {
    mockFetch(routes([invoiceFixture({ stripe_invoice_id: "in_1", invoice_number: "INV-0001" })]));
    renderWithQueryClient(<BillingOverview />);
    const link = await screen.findByRole("link", { name: "INV-0001" });
    assert.equal(link.getAttribute("href"), `/billing/invoices/${invoiceFixture().id}`);
    assert.equal(screen.queryByRole("link", { name: "in_1" }) !== null, false);
});

test("FX3a: an invoice with no number keeps the reference it had", async () => {
    mockFetch(routes([invoiceFixture({ stripe_invoice_id: "in_1", invoice_number: null })]));
    renderWithQueryClient(<BillingOverview />);
    assert.equal((await screen.findByRole("link", { name: "in_1" })) !== null, true);
});

test("FX3a2: the invoice period on the overview uses the app's most used date form", async () => {
    mockFetch(routes([invoiceFixture({ stripe_invoice_id: "in_1", invoice_number: "INV-0001" })]));
    renderWithQueryClient(<BillingOverview />);
    const row = (await screen.findByRole("link", { name: "INV-0001" })).closest("tr")!;
    const text = row.textContent ?? "";
    assert.equal(text.includes(new Date(invoiceFixture().period_start!).toLocaleDateString()), true);
    assert.equal(/[A-Z][a-z]{2} \d{1,2}, 2026/.test(text), false);
});
