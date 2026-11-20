import { test, afterEach, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { cleanup, screen, waitFor } from "@testing-library/react";
import { BillingOverview } from "@/components/billing/billing-overview";
import { BillingPlanSelector } from "@/components/billing/billing-plan-selector";
import { PaypalPlanManage } from "@/components/billing/paypal-plan-manage";
import type { BillingSubscription } from "@/lib/billing-purchase";
import { env } from "@/lib/env";
import { ensureDom } from "@/test-utils/dom";
import { renderWithQueryClient } from "@/test-utils/render";
import { setCheckoutConfirmationPending } from "@/components/billing/use-billing-purchase";

ensureDom();

const originalFetch = globalThis.fetch;
const oldBackend = env.NEXT_PUBLIC_API_BASE_URL;
const ASK_OWNER = "Ask the account owner to manage or cancel the subscription.";
const MONTH = "b1336f1f-6927-4680-8a6b-7b7c86a06e89";
const monthly = { id: MONTH, kind: "stripe" as const, interval: "month" as const, interval_count: 1 as const, amount_minor: 1250, currency: "usd", currency_exponent: 2 as const, active: true, checkout_available: true, unavailable_reason: null, paypal_available: true };
const plan = { id: "starter", name: "Starter", minutes: 100, concurrent_calls: 1, features: ["Calling"], billing_mode: "live", price_options: [monthly] };

const PAYPAL_SUB = {
    status: "active", plan_id: "starter", plan_name: "Starter",
    current_period_start: "2026-11-15T12:00:00Z", current_period_end: "2026-12-15T12:00:00Z",
    cancel_at_period_end: false, minutes_allocated: 100, minutes_used: 0, minutes_remaining: 100,
    billing_portal_available: false, billing_provider: "paypal",
    purchased_price_option: { ...monthly, plan_id: plan.id, plan_name: plan.name },
};
const STRIPE_SUB = { ...PAYPAL_SUB, billing_provider: "stripe", billing_portal_available: true };

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

function routeFetch(permissions: string[] | "never", subscription: unknown) {
    return async (input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
        const path = new URL(url).pathname.replace(/^\/api\/v1/, "");
        if (path === "/rbac/users/me/permissions") {
            if (permissions === "never") return new Promise<Response>(() => {});
            return json({ permissions });
        }
        const routes: Record<string, unknown> = {
            "/billing/subscription": subscription,
            "/billing/plans": [plan],
            "/billing/config": { topups_open: true, paypal_plans_open: true, paypal_plans_unavailable_reason: null },
            "/billing/topups/balance": { allocated: 0, used_minutes: 0, remaining_minutes: 0, unlimited: false, exhausted: false, purchased_minutes: 0, topup_minutes_left: 0 },
            "/billing/usage": { usage_type: "minutes", total_used: 0, allocated: 0, remaining: 0, overage: 0, unlimited: true, metering_period: "calendar_month" },
            "/billing/usage/daily": [], "/billing/invoices": [], "/billing/overage-alerts": [], "/billing/adjustments": [],
        };
        if (!(path in routes)) throw new Error(`unrouted path in test: ${path}`);
        return json(routes[path]);
    };
}

beforeEach(() => { globalThis.fetch = originalFetch; env.NEXT_PUBLIC_API_BASE_URL = "https://backend.example/api/v1"; });
afterEach(() => { setCheckoutConfirmationPending(false); cleanup(); globalThis.fetch = originalFetch; env.NEXT_PUBLIC_API_BASE_URL = oldBackend; });

const noButton = (name: string) => assert.equal(screen.queryByRole("button", { name }) === null, true, `no ${name} button`);

test("overview: a billing user (no billing:admin) gets the ask-the-owner sentence and no Cancel button", async () => {
    globalThis.fetch = routeFetch(["billing:read", "billing:update"], PAYPAL_SUB) as typeof fetch;
    renderWithQueryClient(<BillingOverview />);
    await waitFor(() => assert.equal(screen.queryByText(ASK_OWNER) !== null, true), { timeout: 10_000 });
    noButton("Cancel subscription");
});

test("overview: billing:admin sees the Cancel button and not the sentence", async () => {
    globalThis.fetch = routeFetch(["billing:read", "billing:update", "billing:admin"], PAYPAL_SUB) as typeof fetch;
    renderWithQueryClient(<BillingOverview />);
    await waitFor(() => assert.equal(screen.queryByRole("button", { name: "Cancel subscription" }) !== null, true), { timeout: 10_000 });
    assert.equal(screen.queryByText(ASK_OWNER) === null, true);
});

test("overview: while permissions load neither the button nor the sentence is shown", async () => {
    globalThis.fetch = routeFetch("never", PAYPAL_SUB) as typeof fetch;
    renderWithQueryClient(<BillingOverview />);
    await waitFor(() => assert.equal(screen.queryByText("Current plan") !== null, true), { timeout: 10_000 });
    noButton("Cancel subscription");
    assert.equal(screen.queryByText(ASK_OWNER) === null, true);
});

test("plan selector: a billing user gets the sentence and no Manage subscription button", async () => {
    globalThis.fetch = routeFetch(["billing:read", "billing:update"], STRIPE_SUB) as typeof fetch;
    renderWithQueryClient(<BillingPlanSelector scope="tenant-a:user-a" onNavigate={() => {}} />);
    await waitFor(() => assert.equal(screen.queryByText(ASK_OWNER) !== null, true), { timeout: 10_000 });
    noButton("Manage subscription");
});

test("plan selector: billing:admin sees the Manage subscription button", async () => {
    globalThis.fetch = routeFetch(["billing:admin"], STRIPE_SUB) as typeof fetch;
    renderWithQueryClient(<BillingPlanSelector scope="tenant-a:user-a" onNavigate={() => {}} />);
    await waitFor(() => assert.equal(screen.queryByRole("button", { name: "Manage subscription" }) !== null, true), { timeout: 10_000 });
    assert.equal(screen.queryByText(ASK_OWNER) === null, true);
});

const RETRY = /PayPal tries again by itself/;
const STOPPED = "PayPal stopped this plan after failed payments and will not try again. Cancel it here, then buy the plan again.";

test("an unpaid PayPal plan says PayPal stopped it and will not try again", () => {
    renderWithQueryClient(<PaypalPlanManage subscription={{ ...PAYPAL_SUB, status: "unpaid" } as unknown as BillingSubscription} />);
    assert.equal(screen.queryByText(STOPPED) !== null, true);
    assert.equal(screen.queryByText(RETRY) === null, true);
});

test("a past_due PayPal plan keeps the retry sentence", () => {
    renderWithQueryClient(<PaypalPlanManage subscription={{ ...PAYPAL_SUB, status: "past_due" } as unknown as BillingSubscription} />);
    assert.equal(screen.queryByText(RETRY) !== null, true);
    assert.equal(screen.queryByText(STOPPED) === null, true);
});
