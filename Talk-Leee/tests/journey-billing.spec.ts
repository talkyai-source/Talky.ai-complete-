import { expect, test, type Page, type Route } from "@playwright/test";
import { mockApi, mockDashboardApi, signInAsE2eUser } from "./support/session";

/*
 * P2 journey 4 — billing. A signed-in customer opens Billing, opens the plans
 * and starts a plan purchase; the app goes to the checkout address the server
 * returned. That address is intercepted: the real payment site is never opened.
 *
 * Every request is answered here, in the shape the backend sends (the server
 * code is named beside each answer; BE/ = backend/app/).
 *
 * The purchase needs the app built with NEXT_PUBLIC_API_BASE_URL set
 * (requireBillingBackend, src/lib/billing-purchase.ts:79-81), as the browser
 * job does (http://127.0.0.1:3100/api/v1, see ./support/session).
 *
 * Helpers from ./support/session: signInAsE2eUser (session + page shell),
 * mockDashboardApi (shared reads such as /events), mockApi (GET answers).
 */

test.setTimeout(120_000);

const MONTH_PRICE_ID = "b1336f1f-6927-4680-8a6b-7b7c86a06e89";
const YEAR_PRICE_ID = "bd8cb7f8-78ab-4ac8-bb15-24d9de9d34b0";
const SESSION_ID = "cs_test_journey_0001";
// approvedCheckoutUrl (src/lib/billing-purchase.ts:82-87) accepts only https://checkout.stripe.com.
const CHECKOUT_URL = `https://checkout.stripe.com/c/pay/${SESSION_ID}`;

/*
 * GET /billing/plans: the plan rows with their public price options
 * (BE/api/v1/endpoints/billing.py:881-912; rows built in BE/domain/services/billing_catalog.py:177-258,
 * option fields _PUBLIC_FIELDS :64-73 plus checkout_available / unavailable_reason :226).
 */
const monthly = {
    id: MONTH_PRICE_ID, kind: "stripe", interval: "month", interval_count: 1, amount_minor: 4900,
    currency: "usd", currency_exponent: 2, active: true, checkout_available: true, unavailable_reason: null,
};
const yearly = { ...monthly, id: YEAR_PRICE_ID, interval: "year", amount_minor: 49000 };
const PLAN = {
    id: "starter",
    name: "Starter",
    price: 49,
    description: "For a first calling team",
    minutes: 500,
    agents: 1,
    concurrent_calls: 2,
    features: ["Outbound campaigns"],
    not_included: [],
    popular: false,
    billing_mode: "test",
    price_options: [monthly, yearly],
};

/** Thirty UTC days ending today, no calls on any of them. */
function dailyUsage(nowMs: number) {
    return Array.from({ length: 30 }, (_, i) => ({
        date: new Date(nowMs - (29 - i) * 24 * 60 * 60 * 1000).toISOString().slice(0, 10),
        minutesUsed: 0, secondsUsed: 0, totalCalls: 0, successfulCalls: 0, failedCalls: 0,
    }));
}

function json(route: Route, status: number, body: unknown) {
    return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

/** "/api/v1/billing/plans/" -> "/billing/plans". */
function apiPath(route: Route): string {
    return new URL(route.request().url()).pathname.replace(/^.*?\/api\/v1(?=\/|$)/, "").replace(/\/+$/, "");
}

async function mockBackend(page: Page) {
    const state = { checkouts: [] as Array<{ request_id: string; price_option_id: string }>, checkoutPageOpened: 0 };

    await signInAsE2eUser(page);
    await mockDashboardApi(page);
    await mockApi(page, [
        { path: "/billing/plans", body: [PLAN] },
        // GET /billing/subscription with no subscription yet: SubscriptionResponse, status "inactive"
        // (BE/api/v1/endpoints/billing.py:78-92, answer :365-376).
        {
            path: "/billing/subscription",
            body: {
                status: "inactive", plan_id: null, plan_name: null, current_period_start: null, current_period_end: null,
                cancel_at_period_end: false, minutes_allocated: 0, minutes_used: 0, minutes_remaining: 0,
                minutes_state: "known", purchased_price_option: null, billing_portal_available: false,
            },
        },
        // GET /billing/usage: UsageSummaryResponse (BE/api/v1/endpoints/billing.py:104-113, route :506).
        {
            path: "/billing/usage",
            body: { usage_type: "minutes", total_used: 0, allocated: 0, remaining: 0, overage: 0, unlimited: false, metering_period: "calendar_month" },
        },
        // GET /billing/usage/daily: one row per UTC day for the last 30 days, zeros on days without calls
        // (BE/api/v1/endpoints/billing.py:559-584).
        { path: "/billing/usage/daily", body: () => dailyUsage(Date.now()) },
        { path: "/billing/invoices", body: { invoices: [], count: 0 } }, // BE/api/v1/endpoints/billing.py:535, answer :546
        { path: "/billing/adjustments", body: [] }, // BE/api/v1/endpoints/billing.py:857, answer :869
        { path: "/billing/overage-alerts", body: [] }, // BE/api/v1/endpoints/billing.py:816
        // GET /billing/config (BE/api/v1/endpoints/billing.py:915-949).
        {
            path: "/billing/config",
            body: {
                stripe_configured: true, mock_mode: false, billing_mode: "test", publishable_key: "pk_test_journey",
                checkout_open: true, checkout_unavailable_reason: null, topups_open: true, topups_unavailable_reason: null,
            },
        },
        // Minute top-ups (BE/api/v1/endpoints/billing_topups.py: /packages :162, /balance :200, /orders :366, /ledger :393).
        { path: "/billing/topups/packages", body: [] },
        {
            path: "/billing/topups/balance",
            body: { allocated: 0, used_minutes: 0, remaining_minutes: 0, unlimited: false, exhausted: false, purchased_minutes: 0, topup_minutes_left: 0 },
        },
        { path: "/billing/topups/orders", body: { orders: [] } },
        { path: "/billing/topups/ledger", body: { entries: [] } },
    ]);

    // Registered last, so it runs first: the purchase itself.
    await page.route(
        (url) => /\/api\/v1(?:\/|$)/.test(url.pathname),
        async (route) => {
            const request = route.request();
            if (request.resourceType() === "document") return route.fallback();
            const path = apiPath(route);
            const method = request.method();

            // POST /billing/create-checkout-session: CreateCheckoutResponse (BE/api/v1/endpoints/billing.py:52-61,
            // route :185-227; the attempt is built in BE/domain/services/billing_checkout.py:110-116,
            // price_option by public_option :34-38).
            if (method === "POST" && path === "/billing/create-checkout-session") {
                const body = request.postDataJSON() as { request_id: string; price_option_id: string };
                state.checkouts.push(body);
                const price = body.price_option_id === YEAR_PRICE_ID ? yearly : monthly;
                return json(route, 200, {
                    request_id: body.request_id,
                    state: "open",
                    session_id: SESSION_ID,
                    checkout_url: CHECKOUT_URL,
                    price_option: {
                        id: price.id, plan_id: PLAN.id, plan_name: PLAN.name, kind: price.kind, interval: price.interval,
                        interval_count: price.interval_count, amount_minor: price.amount_minor,
                        currency: price.currency, currency_exponent: price.currency_exponent,
                    },
                    mock_mode: false,
                    message: null,
                });
            }

            // GET /billing/checkout-attempts/{request_id} before any purchase: 404 checkout_not_found
            // (BE/api/v1/endpoints/billing.py:259-287, raised in BE/domain/services/billing_checkout.py:391).
            if (method === "GET" && path.startsWith("/billing/checkout-attempts/")) {
                return json(route, 404, { detail: { code: "checkout_not_found", message: "Checkout request was not found." } });
            }

            return route.fallback();
        },
    );

    // The payment site is never opened: its address is answered here.
    await page.route("https://checkout.stripe.com/**", (route) => {
        state.checkoutPageOpened += 1;
        return route.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html><title>Checkout (intercepted)</title><h1>Checkout (intercepted)</h1>" });
    });

    return state;
}

test("a signed-in customer opens Billing, opens the plans and is sent to the checkout address the server returned", async ({ page }) => {
    const backend = await mockBackend(page);

    await page.goto("/billing");
    await expect(page.getByRole("heading", { level: 1, name: "Billing" })).toBeVisible();

    // Open the plans from the plan tile (billing-overview.tsx:396).
    await page.getByRole("link", { name: "Manage plan" }).click();
    await page.waitForURL(/\/billing\/plans\/?$/);
    await expect(page.getByRole("heading", { level: 1, name: "Plans" })).toBeVisible(); // billing/plans/page.tsx:8

    // The plan the server offers, at the server's own price (billing-plan-selector.tsx:85-87).
    await expect(page.getByText("$49.00 / month")).toBeVisible();
    await expect(page.getByText("Monthly included allowance: 500 minutes")).toBeVisible();

    await page.getByRole("button", { name: `Select ${PLAN.name}` }).click();

    // The app goes to the address the server returned, and nowhere else.
    await page.waitForURL(CHECKOUT_URL);
    await expect(page.getByRole("heading", { name: "Checkout (intercepted)" })).toBeVisible();
    expect(backend.checkoutPageOpened).toBe(1);

    // CreateCheckoutRequest (BE/api/v1/endpoints/billing.py:44-49): a request id and the chosen price, nothing else.
    expect(backend.checkouts).toHaveLength(1);
    expect(Object.keys(backend.checkouts[0] ?? {}).sort()).toEqual(["price_option_id", "request_id"]);
    expect(backend.checkouts[0]?.price_option_id).toBe(MONTH_PRICE_ID);
    expect(backend.checkouts[0]?.request_id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
});
