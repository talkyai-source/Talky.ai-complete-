import { afterEach, beforeEach, test } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { type QueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { env } from "@/lib/env";
import { ApiClientError, type HttpRequestOptions } from "@/lib/http-client";
import { renderWithQueryClient } from "@/test-utils/render";
import { BillingPlanSelector, BillingCheckoutReturn } from "./billing-plan-selector";
import { storeSavedPurchase, type SavedPurchase } from "@/lib/billing-purchase";

const MONTH = "b1336f1f-6927-4680-8a6b-7b7c86a06e89";
const YEAR = "bd8cb7f8-78ab-4ac8-bb15-24d9de9d34b0";
const FREE = "5e080f50-702c-4398-8a0b-f3e4bc053f94";
const REQUEST = "4b011c8a-e911-4198-a9f2-711c2f96d8cc";
const scope = "tenant-a:user-a";
const monthly = { id: MONTH, kind: "stripe" as const, interval: "month" as const, interval_count: 1 as const, amount_minor: 1250, currency: "usd", currency_exponent: 2 as const, active: true, checkout_available: true, unavailable_reason: null };
const annual = { ...monthly, id: YEAR, interval: "year" as const, amount_minor: 13000 };
const plan = { id: "starter", name: "Starter", minutes: 100, concurrent_calls: 1, features: ["Calling"], billing_mode: "live", price_options: [monthly, annual] };
const emptySubscription = { status: "inactive", plan_id: null, plan_name: null, minutes_allocated: 0, minutes_used: 0, minutes_remaining: 0, billing_portal_available: false, purchased_price_option: null };
const snapshot = (price = monthly) => ({ ...price, plan_id: plan.id, plan_name: plan.name });
const originalRequest = api.request;
const oldBackend = env.NEXT_PUBLIC_API_BASE_URL;
const clients: QueryClient[] = [];
let catalogue: unknown = [plan];
let subscription: unknown = emptySubscription;
let writes: HttpRequestOptions[] = [];
let handleWrite: (opts: HttpRequestOptions) => Promise<unknown>;
let handleRead: (opts: HttpRequestOptions) => Promise<unknown>;
let navigations: string[] = [];
function accepted(opts: HttpRequestOptions, state = "open") {
  const body = opts.body as { request_id: string; price_option_id: string };
  const price = body.price_option_id === YEAR ? annual : monthly;
  return { request_id: body.request_id, state, session_id: state === "open" ? "cs_synthetic" : null, checkout_url: state === "open" ? "https://checkout.stripe.com/c/pay/synthetic" : null, price_option: { ...price, plan_id: plan.id, plan_name: plan.name }, mock_mode: false };
}
function mount(activeScope = scope, statusOnly = false) {
  const ui = statusOnly ? <BillingCheckoutReturn key={activeScope} scope={activeScope} onNavigate={url => navigations.push(url)} /> : <BillingPlanSelector key={activeScope} scope={activeScope} onNavigate={url => navigations.push(url)} />;
  const view = renderWithQueryClient(ui); clients.push(view.qc); return view;
}
beforeEach(() => {
  env.NEXT_PUBLIC_API_BASE_URL = "https://backend.example/api/v1";
  catalogue = [plan]; subscription = emptySubscription; writes = []; navigations = [];
  handleWrite = async opts => accepted(opts);
  handleRead = async opts => ({ request_id: opts.path.split("/").at(-1), state: "pending", session_id: null, checkout_url: null, price_option: snapshot(), mock_mode: false });
  api.request = (async (opts: HttpRequestOptions) => {
    if (opts.path === "/billing/plans") return catalogue;
    if (opts.path === "/billing/subscription") return subscription;
    if (opts.path.startsWith("/billing/checkout-attempts/")) return handleRead(opts);
    if (opts.path === "/billing/create-checkout-session" || opts.path === "/billing/portal") { writes.push(opts); return handleWrite(opts); }
    throw new Error(`Unexpected synthetic request ${opts.path}`);
  }) as typeof api.request;
  window.history.replaceState({}, "", "/billing/plans");
});
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); api.request = originalRequest; env.NEXT_PUBLIC_API_BASE_URL = oldBackend; window.sessionStorage.clear(); window.history.replaceState({}, "", "/"); });

test("yearly selection displays the server amount and submits that option only", async () => {
  mount(); fireEvent.click(await screen.findByRole("radio", { name: "Yearly" }));
  assert.ok(screen.getByText("$130.00 / year"));
  assert.equal(screen.queryByText(/17%|Save/), null);
  fireEvent.click(screen.getByRole("button", { name: "Select Starter" }));
  await waitFor(() => assert.equal(navigations.length, 1));
  const body = writes[0]!.body as Record<string, unknown>;
  assert.equal(body.price_option_id, YEAR);
  assert.deepEqual(Object.keys(body).sort(), ["price_option_id", "request_id"]);
  assert.equal(writes[0]!.timeoutMs, 15000);
});

test("unapproved annual offers expose neither a yearly control nor saving claim", async () => {
  catalogue = [{ ...plan, price_options: [monthly, { ...annual, checkout_available: false }] }];
  mount(); await screen.findByRole("button", { name: "Select Starter" });
  assert.equal(screen.queryByRole("radio", { name: "Yearly" }), null);
  assert.ok(screen.getByText("$12.50 / month"));
  assert.equal(screen.queryByText(/Save|17%/), null);
});

test("interval choices are keyboard-operable and yearly charge does not change monthly allowance", async () => {
  catalogue = [{ ...plan, minutes: 0 }];
  mount(); const monthlyRadio = await screen.findByRole("radio", { name: "Monthly" });
  assert.ok(screen.getByRole("group", { name: "Billing interval" }));
  // jsdom lacks CSS.escape; user-event uses it only for this known radio name.
  const cssDescriptor = Object.getOwnPropertyDescriptor(window, "CSS");
  Object.defineProperty(window, "CSS", { configurable: true, value: { escape: (value: string) => { assert.match(value, /^[a-z-]+$/); return value; } } });
  try { monthlyRadio.focus(); await userEvent.setup().keyboard("{ArrowRight}"); }
  finally { if (cssDescriptor) Object.defineProperty(window, "CSS", cssDescriptor); else Reflect.deleteProperty(window, "CSS"); }
  assert.equal((screen.getByRole("radio", { name: "Yearly" }) as HTMLInputElement).checked, true);
  assert.ok(screen.getByText("$130.00 / year"));
  assert.ok(screen.getByText(/Monthly included allowance: Unlimited/));
});

test("blocked session storage prevents an unrecoverable new purchase", async () => {
  const proto = Object.getPrototypeOf(window.sessionStorage), original = proto.getItem;
  try {
    proto.getItem = () => { throw new Error("blocked storage"); };
    mount(); const button = await screen.findByRole("button", { name: "Select Starter" });
    assert.equal((button as HTMLButtonElement).disabled, true);
    assert.ok(screen.getByText(/Enable session storage/));
    fireEvent.click(button); assert.equal(writes.length, 0);
  } finally { proto.getItem = original; }
});

test("synchronous duplicate clicks create one request and do not redirect before receipt", async () => {
  let resolve!: (value: unknown) => void;
  handleWrite = () => new Promise(done => { resolve = done; });
  mount(); const button = await screen.findByRole("button", { name: "Select Starter" });
  act(() => { fireEvent.click(button); fireEvent.click(button); });
  assert.equal(writes.length, 1); assert.equal(navigations.length, 0);
  await act(async () => resolve(accepted(writes[0]!)));
  assert.equal(navigations.length, 1);
});

test("lost response survives reload and retries the identical tenant-scoped request", async () => {
  handleWrite = async () => { throw new Error("Synthetic lost response"); };
  mount(); fireEvent.click(await screen.findByRole("button", { name: "Select Starter" }));
  await screen.findByText("Synthetic lost response"); const original = writes[0]!.body;
  cleanup(); mount(); await screen.findByRole("button", { name: "Resume saved purchase" });
  await waitFor(() => assert.equal((screen.getByRole("button", { name: "Resume saved purchase" }) as HTMLButtonElement).disabled, false));
  assert.equal(writes.length, 1);
  handleWrite = async opts => accepted(opts);
  fireEvent.click(screen.getByRole("button", { name: "Resume saved purchase" }));
  await waitFor(() => assert.equal(navigations.length, 1));
  assert.deepEqual(writes[1]!.body, original);
  cleanup(); mount("tenant-b:user-b"); await screen.findByRole("button", { name: "Select Starter" });
  assert.equal(screen.queryByText(/Saved purchase:/), null);
});

test("browser cancellation keeps the saved request frozen until confirmed terminal", async () => {
  const saved: SavedPurchase = { request_id: REQUEST, price_option: snapshot() }; storeSavedPurchase(scope, saved);
  window.history.replaceState({}, "", "/billing/plans?checkout=cancelled");
  mount(); await screen.findByText(/does not confirm whether payment completed/);
  const select = await screen.findByRole("button", { name: "Select Starter" });
  await waitFor(() => assert.equal((screen.getByRole("button", { name: "Check status" }) as HTMLButtonElement).disabled, false));
  assert.equal((select as HTMLButtonElement).disabled, true); assert.equal(writes.length, 0);
  handleRead = async () => ({ request_id: REQUEST, state: "expired", session_id: "cs_synthetic", checkout_url: null, price_option: snapshot(), mock_mode: false });
  fireEvent.click(screen.getByRole("button", { name: "Check status" }));
  await screen.findByText(/server confirmed the saved checkout expired/);
  assert.equal((screen.getByRole("button", { name: "Select Starter" }) as HTMLButtonElement).disabled, false);
});

test("forged success without a saved receipt never activates or starts a purchase", async () => {
  window.history.replaceState({}, "", "/billing?checkout=success");
  mount(scope, true);
  assert.ok(await screen.findByText(/Returning from checkout does not activate/));
  assert.equal(screen.queryByText(/server confirmed this purchase/i), null);
  assert.equal(writes.length, 0);
});

for (const malformed of ["wrong option", "wrong amount", "mock", "unsafe URL", "unapproved HTTPS host"]) {
  test(`rejects ${malformed} without redirecting or discarding the saved request`, async () => {
    handleWrite = async opts => {
      const response = accepted(opts);
      if (malformed === "wrong option") response.price_option.id = YEAR;
      if (malformed === "wrong amount") response.price_option.amount_minor = 99999;
      if (malformed === "mock") response.mock_mode = true;
      if (malformed === "unsafe URL") response.checkout_url = "javascript:alert('synthetic')";
      if (malformed === "unapproved HTTPS host") response.checkout_url = "https://other.example/checkout";
      return response;
    };
    mount(); fireEvent.click(await screen.findByRole("button", { name: "Select Starter" }));
    await screen.findByRole("alert");
    assert.equal(navigations.length, 0); assert.ok(screen.getByText(/Saved purchase:/));
    assert.equal((screen.getByRole("button", { name: "Select Starter" }) as HTMLButtonElement).disabled, true);
  });
}

test("only the typed pre-provider rejection unlocks a saved purchase", async () => {
  handleWrite = async () => { throw new ApiClientError({ status: 400, code: "billing_offer_unavailable", message: "Offer unavailable", details: { request_not_started: true }, url: "/billing/create-checkout-session", method: "POST" }); };
  mount(); fireEvent.click(await screen.findByRole("button", { name: "Select Starter" }));
  await screen.findByText("Offer unavailable");
  assert.equal(screen.queryByText(/Saved purchase:/), null);
  assert.equal((screen.getByRole("button", { name: "Select Starter" }) as HTMLButtonElement).disabled, false);
});

test("an offer error without explicit pre-insert proof preserves the uncertain request", async () => {
  handleWrite = async () => { throw new ApiClientError({ status: 400, code: "billing_offer_unavailable", message: "Offer unavailable", url: "/billing/create-checkout-session", method: "POST" }); };
  mount(); fireEvent.click(await screen.findByRole("button", { name: "Select Starter" }));
  await screen.findByText("Offer unavailable");
  assert.ok(screen.getByText(/Saved purchase:/)); assert.equal(writes.length, 1);
  assert.equal((screen.getByRole("button", { name: "Select Starter" }) as HTMLButtonElement).disabled, true);
});

for (const status of [400, 403, 409, 503]) {
  test(`generic ${status} and a later missing receipt keep the original purchase frozen`, async () => {
    handleWrite = async () => { throw new ApiClientError({ status, code: "unknown_outcome", message: "Receipt unconfirmed", url: "/billing/create-checkout-session", method: "POST" }); };
    mount(); fireEvent.click(await screen.findByRole("button", { name: "Select Starter" }));
    await screen.findByText("Receipt unconfirmed");
    handleRead = async () => { throw new ApiClientError({ status: 404, code: "not_found", message: "Receipt not found", url: "/billing/checkout-attempts/synthetic", method: "GET" }); };
    fireEvent.click(screen.getByRole("button", { name: "Check status" }));
    await screen.findByText("Receipt not found");
    assert.ok(screen.getByText(/Saved purchase:/));
    assert.equal((screen.getByRole("button", { name: "Select Starter" }) as HTMLButtonElement).disabled, true);
  });
}

test("a verified delayed receipt activates on explicit status check after return", async () => {
  storeSavedPurchase(scope, { request_id: REQUEST, price_option: snapshot() });
  window.history.replaceState({}, "", "/billing?checkout=success"); mount(scope, true);
  await screen.findByText(/Payment confirmation is pending/);
  await waitFor(() => assert.equal((screen.getByRole("button", { name: "Check status" }) as HTMLButtonElement).disabled, false));
  handleRead = async () => ({ request_id: REQUEST, state: "activated", session_id: "cs_synthetic", checkout_url: null, price_option: snapshot(), mock_mode: false });
  fireEvent.click(screen.getByRole("button", { name: "Check status" }));
  await screen.findByText(/server confirmed this purchase/i); assert.equal(writes.length, 0);
});

test("current paid subscriptions use the existing portal with server-owned return URL", async () => {
  subscription = { ...emptySubscription, status: "active", plan_id: plan.id, purchased_price_option: snapshot(), billing_portal_available: true };
  handleWrite = async () => ({ portal_url: "https://billing.stripe.com/p/session/synthetic", mock_mode: false });
  mount(); fireEvent.click(await screen.findByRole("button", { name: "Manage subscription" }));
  await waitFor(() => assert.equal(navigations.length, 1));
  assert.equal(writes[0]!.path, "/billing/portal"); assert.deepEqual(writes[0]!.body, {});
  assert.equal((screen.getByRole("button", { name: "Select Starter" }) as HTMLButtonElement).disabled, true);
});

for (const path of ["checkout", "portal"]) {
  test(`leaving the account aborts ${path} and ignores a delayed response`, async () => {
    let resolve!: (value: unknown) => void;
    handleWrite = () => new Promise(done => { resolve = done; });
    if (path === "portal") subscription = { ...emptySubscription, status: "active", purchased_price_option: snapshot(), billing_portal_available: true };
    const view = mount(); fireEvent.click(await screen.findByRole("button", { name: path === "portal" ? "Manage subscription" : "Select Starter" }));
    assert.equal(writes.length, 1); const signal = writes[0]!.signal;
    view.unmount(); mount("tenant-b:user-b"); assert.equal(signal?.aborted, true);
    await act(async () => resolve(path === "portal" ? { portal_url: "https://billing.stripe.com/p/session/synthetic", mock_mode: false } : accepted(writes[0]!)));
    assert.equal(navigations.length, 0);
  });
}

for (const status of [403, 409]) test(`explicit ${status} pre-insert rejection releases the unsent request without automatic retry`, async () => {
  handleWrite = async () => { throw new ApiClientError({ status, code: "account_unavailable", message: "Manage the current subscription", details: { request_not_started: true }, url: "/billing/create-checkout-session", method: "POST" }); };
  mount(); fireEvent.click(await screen.findByRole("button", { name: "Select Starter" }));
  await screen.findByText("Manage the current subscription");
  assert.equal(screen.queryByText(/Saved purchase:/), null); assert.equal(writes.length, 1);
});

test("another tab's verified outstanding purchase is adopted and only explicitly resumed", async () => {
  const existing = { request_id: REQUEST, state: "open", session_id: "cs_existing", checkout_url: "https://checkout.stripe.com/c/pay/existing", price_option: { ...annual, plan_id: plan.id, plan_name: plan.name }, mock_mode: false };
  handleWrite = async () => { throw new ApiClientError({ status: 409, code: "checkout_outstanding", message: "Resume the existing purchase", details: { request_not_started: true, existing_attempt: existing }, url: "/billing/create-checkout-session", method: "POST" }); };
  mount(); fireEvent.click(await screen.findByRole("button", { name: "Select Starter" }));
  await screen.findByText(`Request reference: ${REQUEST}`); assert.ok(screen.getByText(/Saved purchase: Starter — \$130.00 \/ year/));
  assert.equal(navigations.length, 0); assert.equal(writes.length, 1);
  handleWrite = async () => existing;
  fireEvent.click(screen.getByRole("button", { name: "Resume saved purchase" }));
  await waitFor(() => assert.equal(navigations.length, 1));
  assert.deepEqual(writes[1]!.body, { request_id: REQUEST, price_option_id: YEAR });
});

test("malformed outstanding receipt cannot erase the saved request even with pre-insert proof", async () => {
  handleWrite = async () => { throw new ApiClientError({ status: 409, code: "checkout_outstanding", message: "Resume existing", details: { request_not_started: true, existing_attempt: { request_id: REQUEST } }, url: "/billing/create-checkout-session", method: "POST" }); };
  mount(); fireEvent.click(await screen.findByRole("button", { name: "Select Starter" }));
  await screen.findByText(/existing purchase could not be verified/);
  assert.ok(screen.getByText(/Saved purchase:/)); assert.equal(navigations.length, 0); assert.equal(writes.length, 1);
  assert.equal((screen.getByRole("button", { name: "Select Starter" }) as HTMLButtonElement).disabled, true);
});

test("an explicit free option activates without a payment URL or fabricated yearly cost", async () => {
  const free = { ...monthly, id: FREE, kind: "free", amount_minor: 0 };
  catalogue = [{ ...plan, price_options: [free], billing_mode: "disabled" }];
  handleWrite = async opts => ({ ...accepted(opts, "activated"), price_option: { ...free, plan_id: plan.id, plan_name: plan.name } });
  mount(); fireEvent.click(await screen.findByRole("button", { name: "Choose Starter" }));
  await screen.findByText(/server confirmed this purchase/i);
  assert.ok(screen.getByText("Free")); assert.equal(navigations.length, 0);
});

test("missing backend configuration never submits or creates an uncertain request", async () => {
  env.NEXT_PUBLIC_API_BASE_URL = undefined;
  mount(); fireEvent.click(await screen.findByRole("button", { name: "Select Starter" }));
  await screen.findByText(/service connection is not configured/);
  assert.equal(writes.length, 0); assert.equal(screen.queryByText(/Saved purchase:/), null);
});
