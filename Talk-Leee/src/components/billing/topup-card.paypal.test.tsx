import { StrictMode } from "react";
import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import type { QueryClient } from "@tanstack/react-query";
import { SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import { TopupCard } from "@/components/billing/topup-card";
import type { TopupOrder } from "@/lib/topup-api";
import { AuthProvider } from "@/lib/auth-context";
import { resetBillingRefusal } from "@/lib/billing-refusal";
import { clearFreshLoginGrace, invalidateRequestIdentity } from "@/lib/http-client";
import { renderWithQueryClient } from "@/test-utils/render";

const originalFetch = globalThis.fetch;
const originalConsoleError = console.error;
const clients: QueryClient[] = [];
const balance = { allocated: 1000, used_minutes: 20, remaining_minutes: 980, unlimited: false, exhausted: false, purchased_minutes: 250, topup_minutes_left: 0 };
const ORDER_ID = "00000000-0000-4000-8000-000000000404";
const paidOrder: TopupOrder = { id: ORDER_ID, package_code: "payg_125", minutes: 125, price_cents: 5000, currency: "usd", currency_exponent: 2,
  status: "paid", created_at: "2026-10-01T00:00:00Z", paid_at: "2026-10-01T00:00:01Z", provider_payment_id: "paypal_synthetic",
  refund_details: { detail_status: "unavailable", captured_at: null, source_event_id: null, refunds: null } };
const pendingOrder: TopupOrder = { ...paidOrder, status: "pending", paid_at: null };
const pkg = { code: "payg_125", name: "125 minutes", minutes: 125, price_cents: 5000, currency: "usd", currency_exponent: 2,
  expires_days: null, price_per_minute_cents: 40, audience: "payg" };
const pkg2 = { ...pkg, code: "payg_250", name: "250 minutes", minutes: 250, price_cents: 9000, price_per_minute_cents: 36 };
const ME = { id: "user-a", email: "owner@example.test", role: "owner", tenant_id: "tenant-a", minutes_remaining: 20, minutes_state: "known" };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

const PENDING_TEXT = "PayPal is still confirming this payment. Your minutes are added as soon as it is confirmed.";
const NOT_APPROVED_TEXT = "The PayPal payment was not completed. You have not been charged.";
const FAILED_TEXT = "PayPal could not take this payment. You have not been charged.";
const UNCONFIRMED_TEXT = "We could not confirm the payment yet. If PayPal took it, your minutes are added shortly.";
const PAID_TEXT = "Paid order: 125 minutes. The saved order below confirms this payment.";
const CANCELLED_TEXT = "Checkout was cancelled. No payment was taken for this attempt.";

let reads: Record<string, number> = {};
let bodies: Record<string, unknown[]> = {};
let ordersNow: () => unknown[] = () => [];
let configNow: () => Response = () => json({ topups_open: true });
let checkout: () => Response | Promise<Response> = () => json({ detail: "unexpected checkout write" }, 500);
let capture: () => Response = () => json({ detail: "unexpected capture write" }, 500);
let navigations = 0;

async function bodyOf(input: RequestInfo | URL, init?: RequestInit): Promise<unknown> {
  try {
    const raw = init?.body ?? (input instanceof Request ? await input.clone().text() : undefined);
    return typeof raw === "string" ? JSON.parse(raw) : undefined;
  } catch { return undefined; }
}

const SETTLE = { settlePollMs: 20, settleTimeoutMs: 5000 };
function setup(query: string, orders: unknown[] = [], strict = false) {
  reads = {}; bodies = {}; navigations = 0; ordersNow = () => orders;
  // jsdom cannot navigate away; it reports "navigation" as not implemented, which is the signal a page change was asked for.
  console.error = (...args: unknown[]) => {
    if (args.some((arg) => String(arg).includes("navigation"))) { navigations += 1; return; }
    originalConsoleError(...args);
  };
  globalThis.fetch = async (input, init) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const path = new URL(url).pathname;
    reads[path] = (reads[path] ?? 0) + 1;
    if (init?.method === "POST" || (input instanceof Request && input.method === "POST")) (bodies[path] ??= []).push(await bodyOf(input, init));
    if (path.endsWith("/auth/me")) return json(ME);
    if (path.includes("/auth/")) return json({ detail: "Not authenticated" }, 401);
    if (path.endsWith("/billing/config")) return configNow();
    if (path.endsWith("/paypal/checkout")) return checkout();
    if (path.endsWith("/paypal/capture")) return capture();
    if (path.endsWith("/topups/checkout")) return json({ detail: "stripe route not expected here" }, 500);
    return json(path.endsWith("/balance") ? balance : path.endsWith("/packages") ? [pkg, pkg2] : { orders: ordersNow() });
  };
  const card = <AuthProvider><SearchParamsContext.Provider value={new URLSearchParams(query)}><TopupCard {...SETTLE} /></SearchParamsContext.Provider></AuthProvider>;
  const result = renderWithQueryClient(strict ? <StrictMode>{card}</StrictMode> : card);
  clients.push(result.qc); return result;
}
const count = (suffix: string) => Object.entries(reads).filter(([path]) => path.endsWith(suffix)).reduce((n, [, c]) => n + c, 0);
const sleep = (ms: number) => act(() => new Promise<void>((resolve) => setTimeout(resolve, ms)));
const paypalButtons = () => screen.queryAllByRole("button", { name: /^Pay with PayPal/ });
afterEach(() => {
  cleanup(); globalThis.fetch = originalFetch; console.error = originalConsoleError;
  clients.splice(0).forEach((client) => client.clear()); resetBillingRefusal();
  window.sessionStorage.clear(); window.localStorage.clear(); invalidateRequestIdentity(); clearFreshLoginGrace();
  configNow = () => json({ topups_open: true });
  checkout = () => json({ detail: "unexpected checkout write" }, 500);
  capture = () => json({ detail: "unexpected capture write" }, 500);
});

// ── the button ──
for (const [label, config] of [
  ["closed", { topups_open: true, paypal_topups_open: false, paypal_topups_unavailable_reason: "Not yet." }],
  ["missing from an older server", { topups_open: true }],
] as const) {
  test(`the PayPal button is absent when the config says ${label}`, async () => {
    configNow = () => json(config);
    setup("");
    await screen.findAllByRole("button", { name: /^Buy \d+ minutes/ });
    await sleep(30);
    assert.equal(paypalButtons().length, 0);
    assert.equal(screen.queryByText("Pay with PayPal"), null);
  });
}

test("the PayPal button shows on every bundle when open, named for its bundle, beside Buy", async () => {
  configNow = () => json({ topups_open: true, paypal_topups_open: true });
  setup("");
  const buttons = await screen.findAllByRole("button", { name: /^Pay with PayPal/ });
  assert.equal(buttons.length, 2);
  assert.match(buttons[0].getAttribute("aria-label") ?? "", /125 minutes/);
  assert.match(buttons[1].getAttribute("aria-label") ?? "", /250 minutes/);
  assert.equal(screen.getAllByRole("button", { name: /^Buy \d+ minutes/ }).length, 2);
});

test("pressing it posts the bundle code to the PayPal checkout and goes to PayPal's address", async () => {
  configNow = () => json({ topups_open: true, paypal_topups_open: true });
  checkout = () => json({ order_id: ORDER_ID, paypal_order_id: "5O1", checkout_url: "https://www.paypal.com/checkoutnow?token=5O1", minutes: 125, price_cents: 5000, currency: "usd" });
  setup("");
  fireEvent.click((await screen.findAllByRole("button", { name: /^Pay with PayPal/ }))[0]);
  await act(async () => { await sleep(50); });
  assert.equal(count("/paypal/checkout"), 1);
  assert.deepEqual(Object.entries(bodies).find(([path]) => path.endsWith("/paypal/checkout"))?.[1], [{ package_code: "payg_125" }]);
  assert.equal(navigations, 1, "the browser was sent to the approved address");
  assert.equal(screen.queryByText(/could not be verified/), null);
});

test("both buttons of every bundle are disabled while one payment is starting", async () => {
  configNow = () => json({ topups_open: true, paypal_topups_open: true });
  let release: (response: Response) => void = () => {};
  checkout = () => new Promise<Response>((resolve) => { release = resolve; }) as unknown as Response;
  setup("");
  fireEvent.click((await screen.findAllByRole("button", { name: /^Pay with PayPal/ }))[0]);
  await screen.findByText("Opening PayPal…");
  for (const button of [...screen.getAllByRole("button", { name: /^Buy \d+ minutes/ }), ...screen.getAllByRole("button", { name: /PayPal/ })]) {
    assert.equal((button as HTMLButtonElement).disabled, true, button.getAttribute("aria-label")!);
  }
  await act(async () => { release(json({ detail: "stop" }, 500)); await sleep(20); });
});

test("a payment address that is not PayPal's is refused with a notice and no navigation", async () => {
  configNow = () => json({ topups_open: true, paypal_topups_open: true });
  checkout = () => json({ order_id: ORDER_ID, paypal_order_id: "5O1", checkout_url: "https://evil.example/pay", minutes: 125, price_cents: 5000, currency: "usd" });
  setup("");
  fireEvent.click((await screen.findAllByRole("button", { name: /^Pay with PayPal/ }))[0]);
  await screen.findByText(/The payment destination could not be verified/);
  assert.equal(navigations, 0);
  for (const button of screen.getAllByRole("button", { name: /PayPal/ })) assert.equal((button as HTMLButtonElement).disabled, false);
});

test("a failed PayPal checkout shows the server's words", async () => {
  configNow = () => json({ topups_open: true, paypal_topups_open: true });
  checkout = () => json({ detail: "Package not found" }, 400);
  setup("");
  fireEvent.click((await screen.findAllByRole("button", { name: /^Pay with PayPal/ }))[0]);
  await screen.findByText("Package not found");
  assert.equal(navigations, 0);
});

// ── the return ──
const back = (extra: string) => `topup=success&provider=paypal&order_id=${ORDER_ID}${extra}`;

test("return with paid: one capture, then the saved order confirms the payment", async () => {
  capture = () => json({ order_id: ORDER_ID, status: "paid", minutes: 125 });
  setup(back("&token=T&PayerID=P"), [paidOrder]);
  await screen.findByText(PAID_TEXT);
  assert.equal(count("/paypal/capture"), 1);
  assert.deepEqual(Object.entries(bodies).find(([path]) => path.endsWith("/paypal/capture"))?.[1], [{ order_id: ORDER_ID }]);
});

test("return with paid under strict mode still captures once", async () => {
  capture = () => json({ order_id: ORDER_ID, status: "paid", minutes: 125 });
  setup(back(""), [paidOrder], true);
  await screen.findByText(PAID_TEXT);
  await sleep(40);
  assert.equal(count("/paypal/capture"), 1);
});

test("return with pending: says PayPal is still confirming and keeps polling the saved orders", async () => {
  capture = () => json({ order_id: ORDER_ID, status: "pending", minutes: null });
  setup(back(""), [pendingOrder]);
  await screen.findByText(PENDING_TEXT);
  const before = count("/orders");
  await sleep(100);
  assert.ok(count("/orders") > before, "polling continues");
  assert.equal(count("/paypal/capture"), 1);
  ordersNow = () => [paidOrder];
  await screen.findByText(PAID_TEXT, undefined, { timeout: 2000 });
});

test("return with not_approved says the payment was not completed and stops polling", async () => {
  capture = () => json({ order_id: ORDER_ID, status: "not_approved", minutes: null });
  setup(back(""), [pendingOrder]);
  await screen.findByText(NOT_APPROVED_TEXT);
  await sleep(40);
  const settled = count("/orders");
  await sleep(100);
  assert.equal(count("/orders"), settled);
  assert.equal(screen.queryByText(/does not confirm a payment/), null);
  assert.equal(count("/paypal/capture"), 1);
});

test("return with failed says PayPal could not take the payment", async () => {
  capture = () => json({ order_id: ORDER_ID, status: "failed", minutes: null });
  setup(back(""), [pendingOrder]);
  await screen.findByText(FAILED_TEXT);
  assert.equal(count("/paypal/capture"), 1);
});

test("return when the capture call itself fails says it could not confirm and keeps polling", async () => {
  capture = () => json({ detail: "boom" }, 502);
  setup(back(""), [pendingOrder]);
  await screen.findByText(UNCONFIRMED_TEXT);
  const before = count("/orders");
  await sleep(100);
  assert.ok(count("/orders") > before, "polling continues");
  assert.equal(count("/paypal/capture"), 1);
});

test("return with an unknown capture status is treated as a failed call", async () => {
  capture = () => json({ order_id: ORDER_ID, status: "refunded", minutes: null });
  setup(back(""), [pendingOrder]);
  await screen.findByText(UNCONFIRMED_TEXT);
});

test("cancelled return shows the existing cancelled message and makes no capture call", async () => {
  setup(`topup=cancelled&provider=paypal&order_id=${ORDER_ID}`, [pendingOrder]);
  await screen.findByText(CANCELLED_TEXT);
  await sleep(30);
  assert.equal(count("/paypal/capture"), 0);
});

for (const id of ["", "&order_id=forged", `&order_id=${ORDER_ID}0`]) {
  test(`a PayPal return with ${id === "" ? "no order id" : `order id "${id.slice(10)}"`} makes no capture call and says not completed`, async () => {
    setup(`topup=success&provider=paypal${id}`, [pendingOrder]);
    await screen.findByText(NOT_APPROVED_TEXT);
    await sleep(30);
    assert.equal(count("/paypal/capture"), 0);
  });
}

test("the Stripe return path (no provider) makes no capture call and keeps its cautious text", async () => {
  setup(`topup=success&order_id=${ORDER_ID}`, [pendingOrder]);
  await screen.findByText("Payment unconfirmed");
  assert.ok(screen.getByText(/This browser return does not confirm a payment/));
  await sleep(30);
  assert.equal(count("/paypal/capture"), 0);
  assert.equal(screen.queryByText(NOT_APPROVED_TEXT), null);
});
