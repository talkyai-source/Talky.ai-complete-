import { afterEach, beforeEach, mock, test } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { type QueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { env } from "@/lib/env";
import { ApiClientError, type HttpRequestOptions } from "@/lib/http-client";
import { renderWithQueryClient } from "@/test-utils/render";
import { BillingPlanSelector, BillingCheckoutReturn } from "./billing-plan-selector";
import { readSavedPurchase } from "@/lib/billing-purchase";
import { resetBillingRefusal } from "@/lib/billing-refusal";
import { setCheckoutConfirmationPending } from "./use-billing-purchase";

const MONTH = "b1336f1f-6927-4680-8a6b-7b7c86a06e89";
const REQUEST = "4b011c8a-e911-4198-a9f2-711c2f96d8cc";
const PAYPAL_URL = "https://www.paypal.com/webapps/billing/subscriptions?ba_token=BA-1";
const scope = "tenant-a:user-a";
const monthly = { id: MONTH, kind: "stripe" as const, interval: "month" as const, interval_count: 1 as const, amount_minor: 1250, currency: "usd", currency_exponent: 2 as const, active: true, checkout_available: true, unavailable_reason: null, paypal_available: true };
const plan = { id: "starter", name: "Starter", minutes: 100, concurrent_calls: 1, features: ["Calling"], billing_mode: "live", price_options: [monthly] };
const emptySubscription = { status: "inactive", plan_id: null, plan_name: null, minutes_allocated: 0, minutes_used: 0, minutes_remaining: 0, billing_portal_available: false, purchased_price_option: null };
const paypalSubscription = { status: "active", plan_id: "starter", plan_name: "Starter", current_period_start: "2026-11-15T12:00:00Z", current_period_end: "2026-12-15T12:00:00Z", cancel_at_period_end: false, minutes_allocated: 100, minutes_used: 0, minutes_remaining: 100, billing_portal_available: false, billing_provider: "paypal", purchased_price_option: { ...monthly, plan_id: plan.id, plan_name: plan.name } };
const snapshot = { ...monthly, plan_id: plan.id, plan_name: plan.name };
const originalRequest = api.request;
const oldBackend = env.NEXT_PUBLIC_API_BASE_URL;
const clients: QueryClient[] = [];
let catalogue: unknown = [plan];
let subscription: unknown = emptySubscription;
let config: unknown = { topups_open: true, paypal_plans_open: true, paypal_plans_unavailable_reason: null };
let calls: HttpRequestOptions[] = [];
let handleStart: (opts: HttpRequestOptions) => Promise<unknown>;
let handleRead: (opts: HttpRequestOptions) => Promise<unknown>;
let handleCancel: () => Promise<unknown>;
let navigations: string[] = [];
const attempt = (state: string, extra: Record<string, unknown> = {}, id = REQUEST) => ({ request_id: id, state, session_id: state === "open" ? "I-SYNTHETIC" : null, checkout_url: state === "open" ? PAYPAL_URL : null, price_option: snapshot, mock_mode: false, ...extra });
function mount(statusOnly = false) {
  const ui = statusOnly ? <BillingCheckoutReturn scope={scope} onNavigate={url => navigations.push(url)} /> : <BillingPlanSelector scope={scope} onNavigate={url => navigations.push(url)} />;
  const view = renderWithQueryClient(ui); clients.push(view.qc); return view;
}
const paths = () => calls.map(call => call.path);
beforeEach(() => {
  env.NEXT_PUBLIC_API_BASE_URL = "https://backend.example/api/v1";
  catalogue = [plan]; subscription = emptySubscription; calls = []; navigations = [];
  config = { topups_open: true, paypal_plans_open: true, paypal_plans_unavailable_reason: null };
  handleStart = async opts => attempt("open", {}, (opts.body as { request_id: string }).request_id);
  handleRead = async opts => attempt("pending", {}, opts.path.split("/").at(-1));
  handleCancel = async () => ({ status: "active", cancel_at_period_end: true, mock_mode: false, message: "Your plan is cancelled." });
  api.request = (async (opts: HttpRequestOptions) => {
    calls.push(opts);
    if (opts.path === "/billing/plans") return catalogue;
    if (opts.path === "/billing/subscription") return subscription;
    if (opts.path === "/billing/config") return config;
    if (opts.path === "/billing/paypal/subscriptions/checkout") return handleStart(opts);
    if (opts.path.startsWith("/billing/paypal/subscriptions/")) return handleRead(opts);
    if (opts.path === "/billing/cancel") return handleCancel();
    if (opts.path === "/billing/portal" || opts.path === "/billing/create-checkout-session" || opts.path.startsWith("/billing/checkout-attempts/")) throw new Error(`Stripe route must not be used: ${opts.path}`);
    throw new Error(`Unexpected synthetic request ${opts.path}`);
  }) as typeof api.request;
  window.history.replaceState({}, "", "/billing/plans");
});
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); api.request = originalRequest; env.NEXT_PUBLIC_API_BASE_URL = oldBackend; window.sessionStorage.clear(); window.history.replaceState({}, "", "/"); mock.timers.reset(); resetBillingRefusal(); setCheckoutConfirmationPending(false); });
const flush = () => act(async () => {});
const tick = (ms: number) => act(async () => { mock.timers.tick(ms); });
const RETURN = (outcome: string) => `/billing?checkout=${outcome}&provider=paypal&request_id=${REQUEST}&subscription_id=I-1&ba_token=BA-1&token=T`;

test("the PayPal button shows only when config is open, the option is PayPal-ready and checkout is available", async () => {
  mount(); await screen.findByRole("button", { name: "Select Starter" });
  await screen.findByRole("button", { name: "Pay with PayPal" });
  cleanup(); config = { topups_open: true, paypal_plans_open: false };
  mount(); await screen.findByRole("button", { name: "Select Starter" });
  await flush(); assert.equal(screen.queryByRole("button", { name: "Pay with PayPal" }), null);
  cleanup(); config = { topups_open: true };
  mount(); await screen.findByRole("button", { name: "Select Starter" });
  await flush(); assert.equal(screen.queryByRole("button", { name: "Pay with PayPal" }), null);
  cleanup(); config = { paypal_plans_open: true }; catalogue = [{ ...plan, price_options: [{ ...monthly, paypal_available: false }] }];
  mount(); await screen.findByRole("button", { name: "Select Starter" });
  await flush(); assert.equal(screen.queryByRole("button", { name: "Pay with PayPal" }), null);
  cleanup(); catalogue = [{ ...plan, price_options: [{ ...monthly, checkout_available: false }] }];
  mount(); await screen.findByRole("button", { name: "Select Starter" });
  await flush(); assert.equal(screen.queryByRole("button", { name: "Pay with PayPal" }), null);
});

test("an older server without the PayPal fields shows no PayPal button", async () => {
  const { paypal_available: _omit, ...older } = monthly; void _omit;
  catalogue = [{ ...plan, price_options: [older] }]; config = { topups_open: true };
  mount(); await screen.findByRole("button", { name: "Select Starter" }); await flush();
  assert.equal(screen.queryByRole("button", { name: "Pay with PayPal" }), null);
});

test("the PayPal button is disabled when a subscription already exists", async () => {
  subscription = { ...paypalSubscription, billing_provider: "stripe" };
  mount(); const button = await screen.findByRole("button", { name: "Pay with PayPal" });
  assert.equal((button as HTMLButtonElement).disabled, true);
  assert.equal((screen.getByRole("button", { name: "Select Starter" }) as HTMLButtonElement).disabled, true);
});

test("clicking Pay with PayPal saves the purchase, starts the PayPal flow and goes to PayPal", async () => {
  mount(); fireEvent.click(await screen.findByRole("button", { name: "Pay with PayPal" }));
  await waitFor(() => assert.equal(navigations.length, 1));
  assert.deepEqual(navigations, [PAYPAL_URL]);
  const start = calls.find(call => call.path === "/billing/paypal/subscriptions/checkout")!;
  assert.equal((start.body as { price_option_id: string }).price_option_id, MONTH);
  assert.equal(calls.some(call => call.path === "/billing/create-checkout-session"), false);
  const stored = readSavedPurchase(scope).saved;
  assert.equal(stored?.provider, "paypal");
  assert.equal(stored?.request_id, (start.body as { request_id: string }).request_id);
});

test("an activated answer shows the activated notice and does not navigate", async () => {
  handleStart = async opts => attempt("activated", {}, (opts.body as { request_id: string }).request_id);
  mount(); fireEvent.click(await screen.findByRole("button", { name: "Pay with PayPal" }));
  await screen.findByText(/The server confirmed this purchase/);
  assert.equal(navigations.length, 0);
});

test("a server refusal shows the server's message", async () => {
  handleStart = async () => { throw new ApiClientError({ message: "PayPal is not available for this plan right now.", status: 409, code: "paypal_offer_unavailable", details: { code: "paypal_offer_unavailable", message: "PayPal is not available for this plan right now.", request_not_started: true } } as never); };
  mount(); fireEvent.click(await screen.findByRole("button", { name: "Pay with PayPal" }));
  await screen.findByText("PayPal is not available for this plan right now.");
  assert.equal(navigations.length, 0);
});

test("checkout_outstanding adopts the existing PayPal attempt so it can be resumed", async () => {
  const OTHER = "00000000-0000-4000-8000-000000000009";
  handleStart = async () => { throw new ApiClientError({ message: "You already have a purchase open.", status: 409, code: "checkout_outstanding", details: { code: "checkout_outstanding", message: "You already have a purchase open.", request_not_started: true, existing_attempt: attempt("open", {}, OTHER) } } as never); };
  mount(); fireEvent.click(await screen.findByRole("button", { name: "Pay with PayPal" }));
  await screen.findByRole("button", { name: "Resume saved purchase" });
  assert.equal(readSavedPurchase(scope).saved?.request_id, OTHER);
  assert.equal(readSavedPurchase(scope).saved?.provider, "paypal");
  handleStart = async opts => attempt("open", {}, (opts.body as { request_id: string }).request_id);
  fireEvent.click(screen.getByRole("button", { name: "Resume saved purchase" }));
  await waitFor(() => assert.equal(navigations.length, 1));
  assert.deepEqual(navigations, [PAYPAL_URL]);
});

test("a PayPal return polls the PayPal route, says PayPal, and ends on the activated notice", async () => {
  window.history.replaceState({}, "", RETURN("success"));
  mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  mount(true); await flush();
  screen.getByText("Confirming your purchase with PayPal…");
  assert.equal(screen.queryByText(/with Stripe/), null);
  assert.ok(calls.every(call => call.path === `/billing/paypal/subscriptions/${REQUEST}`));
  handleRead = async () => attempt("activated");
  await tick(3000); await flush();
  assert.ok(screen.getByText(/The server confirmed this purchase/));
  assert.equal(screen.queryByText("Confirming your purchase with PayPal…"), null);
  const before = calls.length; await tick(9000); assert.equal(calls.length, before);
});

test("a PayPal return still pending when polling ends says PayPal has not confirmed it", async () => {
  window.history.replaceState({}, "", RETURN("success"));
  mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  mount(true); await flush();
  await tick(120_000); await flush();
  assert.ok(screen.getByText("PayPal has not confirmed the payment yet. Refresh this page in a minute; you have not been charged twice."));
  assert.equal(screen.queryByText("Confirming your purchase with PayPal…"), null);
});

test("a pending PayPal answer's own message is shown when it has one", async () => {
  window.history.replaceState({}, "", RETURN("success"));
  handleRead = async () => attempt("pending", { message: "PayPal is still checking." });
  mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  mount(true); await flush(); await tick(120_000); await flush();
  assert.ok(screen.getByText("PayPal is still checking."));
});

test("a failed or expired PayPal return says so", async () => {
  window.history.replaceState({}, "", RETURN("success"));
  handleRead = async () => attempt("failed");
  mount(true);
  await screen.findByText(/server confirmed the saved checkout failed/);
});

test("a PayPal cancel return asks the server, then says nothing was charged and leaves the plan buttons usable", async () => {
  window.history.replaceState({}, "", RETURN("cancelled"));
  handleRead = async () => attempt("open");
  mount();
  await screen.findByText("You cancelled at PayPal. Nothing was charged.");
  await waitFor(() => assert.equal((screen.getByRole("button", { name: "Pay with PayPal" }) as HTMLButtonElement).disabled, false));
  assert.equal((screen.getByRole("button", { name: "Select Starter" }) as HTMLButtonElement).disabled, false);
  assert.equal(screen.queryByText(/does not confirm whether/), null);
  assert.equal(calls.filter(call => call.path === `/billing/paypal/subscriptions/${REQUEST}`).length, 1);
  assert.equal(readSavedPurchase(scope).saved, null);
});

test("a PayPal cancel return forgets the saved copy of that request and still asks the server", async () => {
  window.sessionStorage.setItem(`talklee.billing-purchase.v1:${encodeURIComponent(scope)}`, JSON.stringify({ request_id: REQUEST, price_option: snapshot, provider: "paypal" }));
  window.history.replaceState({}, "", RETURN("cancelled"));
  handleRead = async () => attempt("open");
  mount();
  await screen.findByText("You cancelled at PayPal. Nothing was charged.");
  assert.equal(readSavedPurchase(scope).saved, null);
  assert.equal((screen.getByRole("button", { name: "Select Starter" }) as HTMLButtonElement).disabled, false);
});

test("a PayPal cancel return never says nothing was charged when the server shows the purchase went through", async () => {
  window.history.replaceState({}, "", RETURN("cancelled"));
  handleRead = async () => attempt("activated");
  mount();
  await screen.findByText(/The server confirmed this purchase/);
  assert.equal(screen.queryByText("You cancelled at PayPal. Nothing was charged."), null);
});

test("a PayPal cancel return makes no claim about a charge while the server has not answered", async () => {
  window.history.replaceState({}, "", RETURN("cancelled"));
  handleRead = async () => { throw new Error("The server did not answer."); };
  mount();
  await screen.findByText(/You came back from PayPal\. This does not confirm whether a payment was made\./);
  assert.equal(screen.queryByText("You cancelled at PayPal. Nothing was charged."), null);
});

test("a PayPal cancel return whose payment PayPal is still confirming is kept to check again", async () => {
  window.history.replaceState({}, "", RETURN("cancelled"));
  handleRead = async () => attempt("pending");
  mount();
  await screen.findByText(/This does not confirm whether a payment was made\. Check the saved purchase below\./);
  assert.equal(readSavedPurchase(scope).saved?.request_id, REQUEST);
  assert.equal(screen.queryByText("You cancelled at PayPal. Nothing was charged."), null);
});

test("a PayPal return does not trigger the Stripe return handling", async () => {
  window.history.replaceState({}, "", RETURN("success"));
  mount(true); await flush();
  assert.equal(calls.some(call => call.path.startsWith("/billing/checkout-attempts/")), false);
});

test("a PayPal subscriber sees Paid with PayPal, no portal button, and cancels after confirming", async () => {
  subscription = paypalSubscription;
  mount();
  await screen.findByText("Paid with PayPal.");
  assert.equal(screen.queryByRole("button", { name: "Manage subscription" }), null);
  assert.equal(screen.queryByText(/billing portal/), null);
  fireEvent.click(screen.getByRole("button", { name: "Cancel subscription" }));
  assert.ok(screen.getByText(/Cancel your plan\? PayPal stops charging now and your plan stays active until/));
  assert.equal(calls.filter(call => call.path === "/billing/cancel").length, 0);
  fireEvent.click(screen.getByRole("button", { name: "Keep my plan" }));
  assert.equal(calls.filter(call => call.path === "/billing/cancel").length, 0);
  fireEvent.click(screen.getByRole("button", { name: "Cancel subscription" }));
  subscription = { ...paypalSubscription, cancel_at_period_end: true };
  fireEvent.click(screen.getByRole("button", { name: "Yes, cancel" }));
  await screen.findByText("Your plan is cancelled.");
  assert.equal(calls.filter(call => call.path === "/billing/cancel").length, 1);
  assert.equal(calls.find(call => call.path === "/billing/cancel")!.method, "POST");
  await screen.findByText(/Your plan ends on/);
  assert.equal(screen.queryByRole("button", { name: "Cancel subscription" }), null);
  assert.ok(paths().filter(path => path === "/billing/subscription").length >= 2);
});

test("a cancelled-at-period-end PayPal plan shows the end date and no cancel button", async () => {
  subscription = { ...paypalSubscription, cancel_at_period_end: true };
  mount();
  await screen.findByText("Paid with PayPal.");
  assert.ok(screen.getByText(/Your plan ends on .*2026/));
  assert.equal(screen.queryByRole("button", { name: "Cancel subscription" }), null);
});

test("a failed cancellation shows the server's message and keeps the plan unchanged", async () => {
  subscription = paypalSubscription;
  handleCancel = async () => { throw new Error("PayPal did not answer. Your plan is unchanged."); };
  mount();
  fireEvent.click(await screen.findByRole("button", { name: "Cancel subscription" }));
  fireEvent.click(screen.getByRole("button", { name: "Yes, cancel" }));
  await screen.findByText("PayPal did not answer. Your plan is unchanged.");
});

test("a PayPal payment row is named by its PayPal transaction, not an empty number or a raw id", async () => {
  const { invoiceLabel } = await import("./invoice-view");
  const row = { id: "0f0f0f0f-0f0f-4f0f-8f0f-0f0f0f0f0f0f", stripe_invoice_id: null, invoice_number: null, source_reference: "5TY05013RG002845M" } as never;
  assert.equal(invoiceLabel(row), "PayPal payment 5TY05013RG002845M");
  assert.equal(invoiceLabel({ ...(row as object), invoice_number: "INV-1" } as never), "INV-1");
  assert.equal(invoiceLabel({ ...(row as object), source_reference: null } as never), "0f0f0f0f-0f0f-4f0f-8f0f-0f0f0f0f0f0f");
});
