import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import type { QueryClient } from "@tanstack/react-query";
import { SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import { TopupCard } from "@/components/billing/topup-card";
import { topupKeys, type TopupOrder } from "@/lib/topup-api";
import { billingKeys } from "@/lib/billing-api";
import { queryKeys } from "@/lib/api-hooks";
import { AuthProvider } from "@/lib/auth-context";
import { resetBillingRefusal } from "@/lib/billing-refusal";
import { clearFreshLoginGrace, invalidateRequestIdentity } from "@/lib/http-client";
import { renderWithQueryClient } from "@/test-utils/render";
import { QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { createAppQueryClient } from "@/lib/app-query-client";
import { notificationsStore } from "@/lib/notifications";

const originalFetch = globalThis.fetch;
const clients: QueryClient[] = [];
// topup_minutes_left: the schema defaults it, but setQueryData bypasses the schema.
const balance = { allocated: 1000, used_minutes: 20, remaining_minutes: 980, unlimited: false, exhausted: false, purchased_minutes: 250, topup_minutes_left: 0 };
const order: TopupOrder = { id: "00000000-0000-4000-8000-000000000404", package_code: "synthetic", minutes: 250, price_cents: 2500, currency: "gbp", currency_exponent: 2,
  status: "paid", created_at: "2026-10-01T00:00:00Z", paid_at: "2026-10-01T00:00:01Z", provider_payment_id: "pi_synthetic",
  refund_details: { detail_status: "unavailable", captured_at: null, source_event_id: null, refunds: null } };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
// Reads per path, and the orders the next read returns (a polling test moves
// the newest order on between reads); the checkout write answers from `checkout`.
let reads: Record<string, number> = {};
let ordersNow: () => unknown[] = () => [];
let checkout: () => Response = () => json({ detail: "unexpected checkout write in this test" }, 500);
// The real provider: its bootstrap reads /auth/me once (the signed-in owner), and
// a later /auth/me read is the card's own refreshUser({ silent: true }).
const ME = { id: "user-a", email: "owner@example.test", role: "owner", tenant_id: "tenant-a", minutes_remaining: 20, minutes_state: "known" };
// Fast polling with a deadline far beyond the test; DEADLINE is the one case that must expire.
const SETTLE = { settlePollMs: 20, settleTimeoutMs: 5000 };
const DEADLINE = { settlePollMs: 20, settleTimeoutMs: 100 };
function setup(query: string, orders: unknown[] = [], failure?: string, packages: unknown[] = [], settle?: typeof SETTLE) {
  reads = {}; ordersNow = () => orders;
  globalThis.fetch = async (input) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const path = new URL(url).pathname;
    reads[path] = (reads[path] ?? 0) + 1;
    if (path.endsWith("/auth/me")) return json(ME);
    if (path.includes("/auth/")) return json({ detail: "Not authenticated" }, 401);
    if (path.endsWith("/topups/checkout")) return checkout();
    const bad = failure && path.endsWith(failure);
    const data = bad ? { detail: "Synthetic unavailable" } : path.endsWith("/balance") ? balance : path.endsWith("/packages") ? packages : { orders: ordersNow() };
    return json(data, bad ? 503 : 200);
  };
  const result = renderWithQueryClient(<AuthProvider><SearchParamsContext.Provider value={new URLSearchParams(query)}><TopupCard {...settle} /></SearchParamsContext.Provider></AuthProvider>);
  clients.push(result.qc); return result;
}
const count = (suffix: string) => Object.entries(reads).filter(([path]) => path.endsWith(suffix)).reduce((n, [, c]) => n + c, 0);
const sleep = (ms: number) => act(() => new Promise<void>((resolve) => setTimeout(resolve, ms)));
afterEach(() => { cleanup(); globalThis.fetch = originalFetch; clients.splice(0).forEach((client) => client.clear()); resetBillingRefusal(); window.sessionStorage.clear(); window.localStorage.clear(); invalidateRequestIdentity(); clearFreshLoginGrace(); });

for (const query of ["topup=success", "topup=cancelled", "topup=success&mock=true"])
  test(`${query} never establishes payment or nonpayment from a browser return`, async () => {
    setup(query); await screen.findByText("No saved top-up orders recorded.");
    assert.ok(screen.getByText(/This browser return does not confirm a payment/));
    assert.equal(screen.queryByText(/Payment received|payment went through|not been charged|Nothing has been charged/i), null);
  });

test("an unrelated aggregate increase cannot confirm this returned purchase; previously saved order is visible", async () => {
  const result = setup(`topup=success&order_id=${order.id}`, [order]);
  await screen.findByText("Paid order");
  await act(async () => { result.qc.setQueryData(topupKeys.balance(), { ...balance, purchased_minutes: 999 }); });
  assert.equal(screen.queryByText(/Your minutes have been added/), null);
  assert.ok(screen.getByText(`Order reference: ${order.id}`));
  assert.ok(screen.getByText(/Payment: pi_synthetic/));
});

test("an unknown order reference from the URL is not presented as a saved order", async () => {
  setup("topup=success&order_id=forged", [order]); await screen.findByText("Paid order");
  assert.equal(screen.queryByText("Order reference: forged"), null);
});

test("recorded refund accounting and captured provider pending state do not promise bank settlement", async () => {
  setup("", [{ ...order, status: "refunded", refund_details: { detail_status: "complete", captured_at: "2026-10-01T00:05:00Z", source_event_id: "evt_refund", refunds: [{ id: "re_synthetic", status: "pending", amount: 1000, currency: "gbp", currency_exponent: 2, created_at: "2026-10-01T00:04:00Z" }] } }]);
  await screen.findByText("Refund accounting recorded");
  assert.ok(screen.getByText(/Provider status: pending/));
  assert.ok(screen.getByText(/Provider status does not confirm when funds reach a bank/));
  assert.match(screen.getByRole("link", { name: /contact billing@/ }).getAttribute("href")!, /^mailto:/);
});

test("a partial empty refund capture remains visible on a paid order", async () => {
  setup("", [{ ...order, refund_details: { detail_status: "partial", captured_at: "2026-10-01T00:05:00Z", source_event_id: "evt_partial", refunds: [] } }]);
  await screen.findByText("Refund entries unavailable or incomplete.");
  assert.equal(screen.queryByText("No refund entries in the captured records."), null);
});

test("failed balance loading does not claim nothing was charged", async () => {
  setup("", [], "/balance"); await screen.findByText("Your minute balance did not load.");
  assert.equal(screen.queryByText(/Nothing has been charged/i), null);
});

test("malformed successful history is an error instead of no purchases", async () => {
  setup("", [{ id: "broken" }]); await screen.findByText(/Your recent top-ups did not load/);
  assert.equal(screen.queryByText("No saved top-up orders recorded."), null);
});

const paygPackage = { code: "payg_125", name: "125 minutes", minutes: 125, price_cents: 5000, currency: "usd", currency_exponent: 2,
  expires_days: null, price_per_minute_cents: 40, audience: "payg" };
test("an account without a plan is offered pay as you go, not a top-up to a paid plan", async () => {
  setup("", [], undefined, [paygPackage]);
  assert.ok(await screen.findByText("Pay as you go"));
  assert.ok(screen.getByText(/No monthly fee/));
  assert.equal(screen.queryByText(/Add minutes to your paid plan/), null);
  assert.ok(screen.getByText("125"));
  assert.ok(screen.getByText("$50.00"));
});

// ── Task 9: minutes after a top-up (?topup=success settles by polling) ──
const pendingOrder: TopupOrder = { ...order, status: "pending", paid_at: null };
const CAUTIOUS = /This browser return does not confirm a payment/;
const CANCELLED = "Checkout was cancelled. No payment was taken for this attempt.";
const PAID = "Paid order: 250 minutes. The saved order below confirms this payment.";
const FAILED = "Payment failed: the newest saved order was not paid. Check the saved order status below.";

test("topup=success polls orders and balance until the newest order is paid, then refreshes every minute figure once", async () => {
  const view = setup("topup=success", [pendingOrder], undefined, [], SETTLE);
  view.qc.setQueryData(billingKeys.plan(), { seeded: true }); view.qc.setQueryData(billingKeys.usage(), { seeded: true });
  view.qc.setQueryData(billingKeys.dailyUsage(), []); view.qc.setQueryData(queryKeys.dashboardSummary(), { seeded: true });
  await screen.findByText("Payment unconfirmed"); assert.ok(screen.getByText(CAUTIOUS));
  await sleep(70);
  assert.ok(count("/orders") >= 2 && count("/balance") >= 2, `polled orders=${count("/orders")} balance=${count("/balance")}`);
  assert.equal(count("/auth/me"), 1, "no minute refresh before the payment is confirmed");
  ordersNow = () => [order];
  await screen.findByText(PAID, undefined, { timeout: 2000 });
  assert.equal(screen.queryByText(CAUTIOUS) === null, true);
  for (const key of [billingKeys.plan(), billingKeys.usage(), billingKeys.dailyUsage(), queryKeys.dashboardSummary()]) {
    assert.equal(view.qc.getQueryState(key)?.isInvalidated, true, JSON.stringify(key));
  }
  await sleep(30); assert.equal(count("/auth/me"), 2, "settings minutes are refreshed through /auth/me exactly once");
  const settled = count("/orders") + count("/balance");
  await sleep(80); assert.equal(count("/orders") + count("/balance"), settled, "polling stops on the final state");
  assert.ok(screen.getByRole("button", { name: "Refresh saved status" }));
});

test("topup=success with the newest order failed shows Payment failed inside the notice, stops polling and refreshes nothing", async () => {
  setup("topup=success", [{ ...pendingOrder, status: "failed" }], undefined, [], SETTLE);
  await screen.findByText(FAILED);
  assert.equal(screen.queryByText(CAUTIOUS) === null, true);
  const settled = count("/orders") + count("/balance");
  await sleep(80); assert.equal(count("/orders") + count("/balance"), settled);
  assert.equal(count("/auth/me"), 1);
  assert.ok(screen.getByRole("button", { name: "Refresh saved status" }));
});

test("topup=success polling stops at the deadline while still unconfirmed and keeps the cautious text and manual refresh", async () => {
  setup("topup=success", [pendingOrder], undefined, [], DEADLINE);
  await screen.findByText("Payment unconfirmed");
  await sleep(160);
  const atDeadline = count("/orders") + count("/balance");
  assert.ok(atDeadline >= 4, `polled ${atDeadline} reads before the deadline`);
  await sleep(100); assert.equal(count("/orders") + count("/balance"), atDeadline, "no reads after the deadline");
  assert.ok(screen.getByText(CAUTIOUS)); assert.ok(screen.getByRole("button", { name: "Refresh saved status" }));
});

test("topup=success polling stops on unmount", async () => {
  const view = setup("topup=success", [pendingOrder], undefined, [], SETTLE);
  await screen.findByText("Payment unconfirmed"); await sleep(50);
  view.unmount();
  const atUnmount = count("/orders") + count("/balance");
  await sleep(100); assert.equal(count("/orders") + count("/balance"), atUnmount, "no reads after unmount");
});

// ── Task 11: return texts the saved orders can back ──
test("topup=cancelled says no payment was taken only once the orders read shows the newest order unpaid", async () => {
  setup("topup=cancelled", [pendingOrder]);
  await screen.findByText(CANCELLED);
  assert.equal(screen.queryByText(CAUTIOUS) === null, true);
});

test("topup=cancelled keeps the cautious text when the orders read fails", async () => {
  setup("topup=cancelled", [pendingOrder], "/orders");
  await screen.findByText(/Your recent top-ups did not load/);
  assert.ok(screen.getByText(CAUTIOUS)); assert.equal(screen.queryByText(CANCELLED) === null, true);
});

test("topup=cancelled with the newest order paid never claims no payment was taken", async () => {
  setup("topup=cancelled", [order]);
  await screen.findByText("Paid order");
  assert.ok(screen.getByText(CAUTIOUS)); assert.equal(screen.queryByText(CANCELLED) === null, true);
});

// ── Task 10: a refusal the backend can only make after the click ──
const secondPackage = { ...paygPackage, code: "payg_250", name: "250 minutes", minutes: 250, price_cents: 9000, price_per_minute_cents: 36 };
const TOPUPS_OFF = "Minute top-ups are not available yet";
for (const [label, response] of [
  ["503 top-ups unavailable", () => json({ detail: TOPUPS_OFF }, 503)],
  ["403 test_billing_team_only", () => json({ detail: { code: "test_billing_team_only", message: TOPUPS_OFF } }, 403)],
] as const) {
  test(`${label} on Buy disables every Buy button and shows the backend's message once`, async () => {
    checkout = response;
    setup("", [], undefined, [paygPackage, secondPackage]);
    fireEvent.click(await screen.findByRole("button", { name: /^Buy 125 minutes/ }));
    await screen.findByText(TOPUPS_OFF);
    assert.equal(screen.getAllByText(TOPUPS_OFF).length, 1);
    for (const button of screen.getAllByRole("button", { name: /^Buy \d+ minutes/ })) assert.equal((button as HTMLButtonElement).disabled, true, button.getAttribute("aria-label")!);
    assert.equal(count("/topups/checkout"), 1);
  });
}

// The app's own query client raises the error toasts; the test client above does not.
function setupWithToasts(packages: unknown[]) {
  reads = {}; ordersNow = () => [];
  globalThis.fetch = async (input) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const path = new URL(url).pathname;
    if (path.endsWith("/auth/me")) return json(ME);
    if (path.includes("/auth/")) return json({ detail: "Not authenticated" }, 401);
    if (path.endsWith("/topups/checkout")) return checkout();
    return json(path.endsWith("/balance") ? balance : path.endsWith("/packages") ? packages : { orders: [] });
  };
  notificationsStore.setIdentity({ tenantId: "tenant-a", userId: "user-a" });
  const qc = createAppQueryClient(() => {});
  // Only the cache lifetime is changed: a finite gcTime keeps node --test alive after the tests.
  qc.setDefaultOptions({ ...qc.getDefaultOptions(), queries: { ...qc.getDefaultOptions().queries, gcTime: Infinity }, mutations: { ...qc.getDefaultOptions().mutations, gcTime: Infinity } });
  clients.push(qc);
  return render(<QueryClientProvider client={qc}><AuthProvider><SearchParamsContext.Provider value={new URLSearchParams("")}><TopupCard /></SearchParamsContext.Provider></AuthProvider></QueryClientProvider>);
}
const toasts = () => notificationsStore.getSnapshot().toasts;

test("a refused Buy shows the backend's message once, inline, with no toast", async () => {
  checkout = () => json({ detail: TOPUPS_OFF }, 503);
  setupWithToasts([paygPackage]);
  fireEvent.click(await screen.findByRole("button", { name: /^Buy 125 minutes/ }));
  await screen.findByText(TOPUPS_OFF);
  await act(async () => {});
  assert.equal(screen.getAllByText(TOPUPS_OFF).length, 1);
  assert.deepEqual(toasts().map((t) => `${t.title}: ${t.message}`), [], "no toast for the four recorded refusals");
  notificationsStore.setIdentity(null);
});

test("an unrelated Buy error still raises its toast", async () => {
  checkout = () => json({ detail: "Package not found" }, 400);
  setupWithToasts([paygPackage]);
  fireEvent.click(await screen.findByRole("button", { name: /^Buy 125 minutes/ }));
  await screen.findByText("Package not found");
  await act(async () => {});
  assert.deepEqual(toasts().map((t) => `${t.title}: ${t.message}`), ["Request failed: Package not found"]);
  notificationsStore.setIdentity(null);
});

test("a 400 on Buy keeps today's behaviour: the message shows and the Buy buttons stay enabled", async () => {
  checkout = () => json({ detail: "Package not found" }, 400);
  setup("", [], undefined, [paygPackage, secondPackage]);
  fireEvent.click(await screen.findByRole("button", { name: /^Buy 125 minutes/ }));
  await screen.findByText("Package not found");
  for (const button of screen.getAllByRole("button", { name: /^Buy \d+ minutes/ })) assert.equal((button as HTMLButtonElement).disabled, false);
});
