import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { api } from "./api";
import { env } from "./env";
import type { HttpRequestOptions } from "./http-client";
import type { SavedPurchase } from "./billing-purchase";
import { cancelPaypalPlan, paypalPlansConfigSchema, paypalPlanReturnFromUrl, readPaypalPlanCheckout, startPaypalPlanCheckout } from "./paypal-plan-api";

const original = api.request;
const originalBase = env.NEXT_PUBLIC_API_BASE_URL;
afterEach(() => { api.request = original; env.NEXT_PUBLIC_API_BASE_URL = originalBase; });
const REQUEST = "4b011c8a-e911-4198-a9f2-711c2f96d8cc";
const price = { id: "b1336f1f-6927-4680-8a6b-7b7c86a06e89", plan_id: "starter", plan_name: "Starter", kind: "stripe" as const, amount_minor: 1250, currency: "usd", currency_exponent: 2 as const, interval: "month" as const, interval_count: 1 as const };
const saved: SavedPurchase = { request_id: REQUEST, price_option: price, provider: "paypal" };
const PAYPAL_URL = "https://www.paypal.com/webapps/billing/subscriptions?ba_token=BA-1";
const attempt = (over: Record<string, unknown> = {}) => ({ request_id: REQUEST, state: "open", session_id: "I-SYNTHETIC", checkout_url: PAYPAL_URL, price_option: price, mock_mode: false, ...over });
function serve(answer: unknown) {
  env.NEXT_PUBLIC_API_BASE_URL = "https://backend.example/api/v1";
  const calls: HttpRequestOptions[] = [];
  api.request = (async (opts: HttpRequestOptions) => { calls.push(opts); return answer; }) as typeof api.request;
  return calls;
}

test("start posts the request id and price option to the PayPal route and accepts a PayPal address", async () => {
  const calls = serve(attempt());
  const result = await startPaypalPlanCheckout(saved);
  assert.equal(calls[0]!.path, "/billing/paypal/subscriptions/checkout");
  assert.equal(calls[0]!.method, "POST");
  assert.deepEqual(calls[0]!.body, { request_id: REQUEST, price_option_id: price.id });
  assert.equal(result.checkout_url, PAYPAL_URL);
  assert.equal(result.state, "open");
});

test("the sandbox PayPal host is accepted", async () => {
  serve(attempt({ checkout_url: "https://www.sandbox.paypal.com/webapps/billing/subscriptions?ba_token=BA-1" }));
  assert.equal((await startPaypalPlanCheckout(saved)).state, "open");
});

for (const url of ["https://checkout.stripe.com/c/pay/x", "https://evil.example/paypal", "http://www.paypal.com/x", "https://www.paypal.com.evil.example/x"]) {
  test(`start refuses the address ${url}`, async () => {
    serve(attempt({ checkout_url: url }));
    await assert.rejects(startPaypalPlanCheckout(saved), /could not be verified/);
  });
}

test("start refuses a different request id or a different price option", async () => {
  serve(attempt({ request_id: "00000000-0000-4000-8000-000000000001" }));
  await assert.rejects(startPaypalPlanCheckout(saved), /do not match your saved selection/);
  serve(attempt({ price_option: { ...price, amount_minor: 1 } }));
  await assert.rejects(startPaypalPlanCheckout(saved), /do not match your saved selection/);
});

test("read asks the PayPal route by request id and refuses another id", async () => {
  const calls = serve(attempt({ state: "pending", session_id: null, checkout_url: null }));
  const result = await readPaypalPlanCheckout(REQUEST);
  assert.equal(calls[0]!.path, `/billing/paypal/subscriptions/${REQUEST}`);
  assert.equal(result.state, "pending");
  serve(attempt({ request_id: "00000000-0000-4000-8000-000000000001" }));
  await assert.rejects(readPaypalPlanCheckout(REQUEST), /does not match the request reference/);
});

test("read refuses an answer that is not an attempt", async () => {
  serve({ state: "activated" });
  await assert.rejects(readPaypalPlanCheckout(REQUEST), /could not be verified/);
});

test("cancel posts to /billing/cancel with no body and validates the answer", async () => {
  const calls = serve({ status: "active", cancel_at_period_end: true, mock_mode: false, message: "Cancelled." });
  const result = await cancelPaypalPlan();
  assert.equal(calls[0]!.path, "/billing/cancel");
  assert.equal(calls[0]!.method, "POST");
  assert.equal(calls[0]!.body, undefined);
  assert.equal(result.cancel_at_period_end, true);
  assert.equal(result.message, "Cancelled.");
  serve({ status: "active", cancel_at_period_end: true, mock_mode: false });
  assert.equal((await cancelPaypalPlan()).message, undefined);
  serve({ status: "active", cancel_at_period_end: "yes", mock_mode: false });
  await assert.rejects(cancelPaypalPlan(), /could not be verified/);
  serve({ status: "active", cancel_at_period_end: true, mock_mode: true });
  await assert.rejects(cancelPaypalPlan(), /could not be verified/);
});

test("config fields default to closed when absent or malformed", () => {
  assert.deepEqual(paypalPlansConfigSchema.parse({ topups_open: true }), { paypal_plans_open: false, paypal_plans_unavailable_reason: null });
  assert.equal(paypalPlansConfigSchema.parse({ paypal_plans_open: "yes" }).paypal_plans_open, false);
  assert.deepEqual(paypalPlansConfigSchema.parse({ paypal_plans_open: true, paypal_plans_unavailable_reason: null }), { paypal_plans_open: true, paypal_plans_unavailable_reason: null });
});

const table: [string, string, ReturnType<typeof paypalPlanReturnFromUrl>][] = [
  ["success", `?checkout=success&provider=paypal&request_id=${REQUEST}&subscription_id=I-1&ba_token=BA-1&token=T`, { outcome: "success", requestId: REQUEST }],
  ["cancelled", `?checkout=cancelled&provider=paypal&request_id=${REQUEST}`, { outcome: "cancelled", requestId: REQUEST }],
  ["upper-case id is normalised", `?checkout=success&provider=paypal&request_id=${REQUEST.toUpperCase()}`, { outcome: "success", requestId: REQUEST }],
  ["missing provider", `?checkout=success&request_id=${REQUEST}`, null],
  ["other provider", `?checkout=success&provider=stripe&request_id=${REQUEST}`, null],
  ["bad uuid", "?checkout=success&provider=paypal&request_id=not-a-uuid", null],
  ["missing request id", "?checkout=success&provider=paypal", null],
  ["unknown outcome", `?checkout=done&provider=paypal&request_id=${REQUEST}`, null],
  ["empty", "", null],
];
for (const [name, search, expected] of table) test(`paypalPlanReturnFromUrl: ${name}`, () => assert.deepEqual(paypalPlanReturnFromUrl(search), expected));
