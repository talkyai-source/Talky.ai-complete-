import { test, afterEach, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { cleanup, screen, waitFor } from "@testing-library/react";
import { BillingOverview } from "@/components/billing/billing-overview";
import { ensureDom } from "@/test-utils/dom";
import { renderWithQueryClient } from "@/test-utils/render";

ensureDom();

/**
 * The billing page must keep three states visibly apart:
 *
 *   loading  — we do not know yet
 *   error    — we asked and did not get an answer
 *   empty    — we asked, got an answer, and the answer is "nothing"
 *
 * It used to collapse error into empty, because `billingFetch` swallowed every
 * failure into `null`. A 403 or a backend outage then rendered as
 * "0 of 0 minutes used" with "No invoices yet" — a confident, wrong statement
 * about a customer's money.
 */

const originalFetch = globalThis.fetch;

const EMPTY_SUBSCRIPTION = {
    status: "inactive",
    plan_id: null,
    plan_name: null,
    current_period_start: null,
    current_period_end: null,
    cancel_at_period_end: false,
    minutes_allocated: 0,
    minutes_used: 0,
    minutes_remaining: 0,
};

const EMPTY_USAGE = {
    usage_type: "minutes",
    total_used: 0,
    allocated: 0,
    remaining: 0,
    overage: 0,
};

function json(body: unknown, status = 200) {
    return new Response(JSON.stringify(body), {
        status,
        headers: { "content-type": "application/json" },
    });
}

/** Route by path so one endpoint can fail while the rest succeed. */
function routeFetch(routes: Record<string, () => Response>) {
    return async (input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
        const path = new URL(url).pathname.replace(/^\/api\/v1/, "");
        const handler = routes[path];
        if (!handler) throw new Error(`unrouted billing path in test: ${path}`);
        return handler();
    };
}

const ALL_EMPTY_OK: Record<string, () => Response> = {
    "/billing/subscription": () => json(EMPTY_SUBSCRIPTION),
    "/billing/usage": () => json(EMPTY_USAGE),
    "/billing/usage/daily": () => json([]),
    "/billing/invoices": () => json([]),
    "/billing/overage-alerts": () => json([]),
    "/billing/adjustments": () => json([]),
};

beforeEach(() => {
    globalThis.fetch = originalFetch;
});

afterEach(() => {
    cleanup();
    globalThis.fetch = originalFetch;
});

test("a failed billing request renders the error state, never '0 of 0 minutes used'", async () => {
    globalThis.fetch = routeFetch(
        Object.fromEntries(Object.keys(ALL_EMPTY_OK).map((p) => [p, () => json({ detail: "Forbidden" }, 403)])),
    ) as typeof fetch;

    renderWithQueryClient(<BillingOverview />);

    await waitFor(() => {
        assert.ok(screen.getByText("Billing data did not load"));
    });

    // The whole point of the fix: no fabricated zeros anywhere on the page.
    assert.equal(screen.queryByText(/minutes used/), null);
    assert.equal(screen.queryByText("No invoices yet."), null);
    assert.equal(screen.queryByText("No call activity in the last 30 days."), null);
    assert.ok(screen.getByRole("alert"));
});

test("a genuinely empty successful response renders the empty state, not an error", async () => {
    globalThis.fetch = routeFetch(ALL_EMPTY_OK) as typeof fetch;

    renderWithQueryClient(<BillingOverview />);

    await waitFor(() => {
        assert.ok(screen.getByText("No invoices yet."));
    });

    assert.ok(screen.getByText("No call activity in the last 30 days."));
    assert.ok(screen.getByText("of 0 minutes used"));
    assert.equal(screen.queryByText("Billing data did not load"), null);
    assert.equal(screen.queryByRole("alert"), null);
});

test("loading is distinct from both the error and the empty state", async () => {
    // Never resolves — the page stays in flight.
    globalThis.fetch = (() => new Promise<Response>(() => {})) as unknown as typeof fetch;

    renderWithQueryClient(<BillingOverview />);

    await waitFor(() => {
        assert.ok(screen.getByText(/Loading billing data/));
    });

    assert.equal(screen.queryByText("Billing data did not load"), null);
    assert.equal(screen.queryByText("No invoices yet."), null);
    assert.equal(screen.queryByText(/minutes used/), null);
});

test("a failed invoices request never renders 'No invoices yet.'", async () => {
    globalThis.fetch = routeFetch({
        ...ALL_EMPTY_OK,
        "/billing/invoices": () => json({ detail: "boom" }, 500),
    }) as typeof fetch;

    renderWithQueryClient(<BillingOverview />);

    await waitFor(() => {
        assert.ok(screen.getByText("Your invoices did not load."));
    });

    assert.equal(screen.queryByText("No invoices yet."), null);
});

test("a failed daily-usage request never renders zeroed call stats", async () => {
    globalThis.fetch = routeFetch({
        ...ALL_EMPTY_OK,
        "/billing/usage/daily": () => json({ detail: "boom" }, 500),
    }) as typeof fetch;

    renderWithQueryClient(<BillingOverview />);

    await waitFor(() => {
        assert.ok(screen.getByText("Call stats did not load."));
    });

    assert.ok(screen.getByText("Daily usage did not load."));
    assert.equal(screen.queryByText("No call activity in the last 30 days."), null);
    assert.equal(screen.queryByText("Total Calls"), null);
});

test("a failed overage-alerts request is surfaced instead of silently showing no alerts", async () => {
    globalThis.fetch = routeFetch({
        ...ALL_EMPTY_OK,
        "/billing/overage-alerts": () => json({ detail: "boom" }, 500),
    }) as typeof fetch;

    renderWithQueryClient(<BillingOverview />);

    await waitFor(() => {
        assert.ok(screen.getByText("Overage alerts did not load."));
    });
});
