import { test, afterEach, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import { BillingOverview } from "@/components/billing/billing-overview";
import { ensureDom } from "@/test-utils/dom";
import { renderWithQueryClient } from "@/test-utils/render";
import { formatMinorMoney, ledgerListSchema } from "@/lib/billing-read";

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
    unlimited: true,
    metering_period: "calendar_month",
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
    assert.ok(screen.getByText("minutes used · Unlimited allowance"));
    assert.equal(screen.queryByText("Billing data did not load"), null);
    assert.equal(screen.queryByRole("alert"), null);
});

test("current purchased terms come from the subscription snapshot and cancellation is not a promised invoice", async () => {
    globalThis.fetch = routeFetch({ ...ALL_EMPTY_OK, "/billing/subscription": () => json({
        ...EMPTY_SUBSCRIPTION, status: "active", plan_id: "starter", plan_name: "Starter", cancel_at_period_end: true,
        current_period_start: "2026-10-01T00:00:00Z", current_period_end: "2027-10-01T00:00:00Z",
        purchased_price_option: { id: "bd8cb7f8-78ab-4ac8-bb15-24d9de9d34b0", plan_id: "starter", plan_name: "Starter", kind: "stripe", interval: "year", interval_count: 1, amount_minor: 123456, currency: "kwd", currency_exponent: 3 },
    }) }) as typeof fetch;
    renderWithQueryClient(<BillingOverview />);
    await screen.findByText(/Purchased offer:.*123\.456.*year/);
    assert.ok(screen.getByText(/Access period ends:/));
    assert.equal(screen.queryByText(/Next invoice:/), null);
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

test("unlimited minutes keep actual usage without a false zero allowance or percentage", async () => {
    globalThis.fetch = routeFetch({ ...ALL_EMPTY_OK, "/billing/usage": () => json({ ...EMPTY_USAGE, total_used: 123, unlimited: true }) }) as typeof fetch;
    renderWithQueryClient(<BillingOverview />);
    await screen.findByText("minutes used · Unlimited allowance");
    assert.ok(screen.getByText("123"));
    assert.equal(screen.queryByText("of 0 minutes used"), null);
    assert.equal(screen.queryByText("0.0% used"), null);
    assert.equal(screen.queryByRole("progressbar"), null);
});

test("usage without a contracted rate does not invent an estimated charge", async () => {
    globalThis.fetch = routeFetch({ ...ALL_EMPTY_OK, "/billing/overage-alerts": () => json([{ type: "minutes", currentUsage: 110, limit: 100, exceededBy: 10, estimatedCharge: null, currency: null, currency_exponent: null, severity: "critical" }]) }) as typeof fetch;
    renderWithQueryClient(<BillingOverview />);
    await screen.findByText(/Additional usage pricing is unavailable/);
    assert.equal(screen.queryByText(/Estimated overage charge|\$1\.00/), null);
});

test("linked refund movement preserves negative accounting amounts without a bank-refund claim", async () => {
    globalThis.fetch = routeFetch({ ...ALL_EMPTY_OK, "/billing/adjustments": () => json([{ id: "12", order_id: "00000000-0000-4000-8000-000000000404", provider_event_id: "evt_refund", provider_payment_id: "pi_original", kind: "refund", minutes_delta: -250, amount_cents: -2500, currency: "gbp", currency_exponent: 2, note: null, created_at: "2026-10-01T00:00:00Z" }]) }) as typeof fetch;
    renderWithQueryClient(<BillingOverview />);
    await screen.findByText("Refund accounting reversal");
    assert.ok(screen.getByText("-£25.00"));
    assert.ok(screen.getByText("pi_original"));
    assert.ok(screen.getByText(/A reversal is not proof that funds reached a bank/));
});

test("malformed successful usage is a load failure instead of zero consumption", async () => {
    globalThis.fetch = routeFetch({ ...ALL_EMPTY_OK, "/billing/usage": () => json({}) }) as typeof fetch;
    renderWithQueryClient(<BillingOverview />);
    await screen.findByText("Billing data did not load");
    assert.equal(screen.queryByText("of 0 minutes used"), null);
});

test("an unlimited subscription does not duplicate a false zero remaining or percentage meter", async () => {
    globalThis.fetch = routeFetch({ ...ALL_EMPTY_OK, "/billing/subscription": () => json({ ...EMPTY_SUBSCRIPTION, status: "active", plan_id: "unlimited", plan_name: "Unlimited plan", minutes_used: 99999 }),
        "/billing/usage": () => json({ ...EMPTY_USAGE, total_used: 37 }) }) as typeof fetch;
    renderWithQueryClient(<BillingOverview />);
    await screen.findByText("Unlimited plan");
    assert.ok(within(screen.getByText("Included minutes").parentElement!).queryByText("Unlimited"), "zero-sentinel subscription allowance must say Unlimited");
    assert.equal(screen.queryByText("Minutes remaining"), null);
    assert.equal(screen.queryByText(/0% used|99,999/), null);
    assert.equal(screen.queryByRole("progressbar"), null);
});

test("a missing subscription allowance is a read failure rather than an inferred unlimited plan", async () => {
    const incomplete = { ...EMPTY_SUBSCRIPTION } as Partial<typeof EMPTY_SUBSCRIPTION>;
    delete incomplete.minutes_allocated;
    globalThis.fetch = routeFetch({ ...ALL_EMPTY_OK, "/billing/subscription": () => json(incomplete) }) as typeof fetch;
    renderWithQueryClient(<BillingOverview />);
    await screen.findByText("Billing data did not load");
    assert.equal(screen.queryByText("Unlimited"), null);
});

test("average settled time preserves a recorded 59-second call instead of reconstructing rounded minutes", async () => {
    globalThis.fetch = routeFetch({ ...ALL_EMPTY_OK, "/billing/usage/daily": () => json([{ date: "2026-10-04", minutesUsed: 0, secondsUsed: 59, totalCalls: 1, successfulCalls: 1, failedCalls: 0 }]) }) as typeof fetch;
    renderWithQueryClient(<BillingOverview />);
    await screen.findByText("Total Calls");
    assert.ok(screen.queryByText("0m 59s"), "59 recorded seconds must not become zero duration");
    assert.ok(screen.queryByText("Average settled time per call"));
    assert.equal(screen.queryByText("Avg Duration"), null);
});

test("missing exact daily seconds is an unavailable call-stat read, never a guessed zero", async () => {
    globalThis.fetch = routeFetch({ ...ALL_EMPTY_OK, "/billing/usage/daily": () => json([{ date: "2026-10-04", minutesUsed: 0, totalCalls: 1, successfulCalls: 1, failedCalls: 0 }]) }) as typeof fetch;
    renderWithQueryClient(<BillingOverview />);
    await screen.findByText("Call stats did not load.");
    assert.equal(screen.queryByText("0m 0s"), null);
});

test("actual PostgreSQL ledger endpoints agree and render signed linked accounting movements", async () => {
    const evidence = JSON.parse(readFileSync(new URL("../../../../docs/sessions/artifacts/cp04/ledger-contract.json", import.meta.url), "utf8"));
    const entries = ledgerListSchema.parse(evidence.api_adjustments);
    assert.deepEqual(ledgerListSchema.parse(evidence.api_ledger), entries);
    assert.ok(entries.some((entry) => entry.kind === "refund" && entry.minutes_delta < 0));
    assert.ok(entries.some((entry) => entry.kind === "topup" && entry.minutes_delta > 0));
    globalThis.fetch = routeFetch({ ...ALL_EMPTY_OK, "/billing/adjustments": () => json(evidence.api_adjustments) }) as typeof fetch;
    renderWithQueryClient(<BillingOverview />);
    await screen.findByText("Refund accounting reversal");
    const table = screen.getByRole("table", { name: "Recorded top-up accounting entries" });
    for (const entry of entries) {
        const row = within(table).getByText(formatMinorMoney(entry.amount_cents, entry.currency, entry.currency_exponent)).closest("tr")!;
        assert.ok(within(row).getByText(entry.order_id!));
        assert.ok(within(row).getByText(entry.provider_payment_id!));
    }
    assert.ok(screen.getByText(/A reversal is not proof that funds reached a bank/));
});
