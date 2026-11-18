import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { api } from "./api";
import { env } from "./env";
import { approvedCheckoutUrl, billingCatalogSchema, billingSubscriptionSchema, formatPurchasedPrice, hasManagedSubscription, savedPurchaseSchema, startBillingCheckout, yearlySaving, type SavedPurchase } from "./billing-purchase";
import { createHttpClient, __resetRefreshStateForTests } from "./http-client";
const original = api.request;
const originalFetch = globalThis.fetch;
const originalBase = env.NEXT_PUBLIC_API_BASE_URL;
afterEach(() => { api.request = original; globalThis.fetch = originalFetch; env.NEXT_PUBLIC_API_BASE_URL = originalBase; __resetRefreshStateForTests(); });
const saved: SavedPurchase = { request_id: "4b011c8a-e911-4198-a9f2-711c2f96d8cc", price_option: { id: "b1336f1f-6927-4680-8a6b-7b7c86a06e89", plan_id: "starter", plan_name: "Starter", kind: "stripe", amount_minor: 1250, currency: "usd", currency_exponent: 2, interval: "month", interval_count: 1 } };
test("amount formatting honors server currency exponent rather than a cents assumption", () => {
  assert.equal(formatPurchasedPrice(saved.price_option), "$12.50 / month");
  assert.match(formatPurchasedPrice({ ...saved.price_option, currency: "jpy", currency_exponent: 0 }), /1,250/);
  assert.match(formatPurchasedPrice({ ...saved.price_option, currency: "kwd", currency_exponent: 3 }), /1\.250/);
  assert.equal(formatPurchasedPrice({ ...saved.price_option, kind: "free", amount_minor: 0 }), "Free");
});
test("pending provider outcome keeps the same request and does not invent a checkout URL", async () => {
  env.NEXT_PUBLIC_API_BASE_URL = "https://backend.example/api/v1";
  api.request = (async () => ({ request_id: saved.request_id, state: "pending", session_id: null, checkout_url: null, price_option: saved.price_option, mock_mode: false })) as typeof api.request;
  const result = await startBillingCheckout(saved); assert.equal(result.state, "pending"); assert.equal(result.checkout_url, null);
});

test("actual shared auth client refreshes one 401 without changing the purchase identity", async () => {
  env.NEXT_PUBLIC_API_BASE_URL = "https://backend.example/api/v1";
  const client = createHttpClient({ baseUrl: env.NEXT_PUBLIC_API_BASE_URL });
  api.request = client.request as typeof api.request;
  const bodies: string[] = []; let refreshes = 0;
  globalThis.fetch = async (url, init) => {
    if (String(url).endsWith("/auth/refresh")) { refreshes++; return Response.json({}); }
    bodies.push(String(init?.body));
    if (bodies.length === 1) return Response.json({ detail: "Expired access" }, { status: 401 });
    return Response.json({ request_id: saved.request_id, state: "pending", session_id: null, checkout_url: null, price_option: saved.price_option, mock_mode: false });
  };
  await startBillingCheckout(saved);
  assert.equal(refreshes, 1); assert.equal(bodies.length, 2); assert.equal(bodies[0], bodies[1]);
  assert.deepEqual(JSON.parse(bodies[0]!), { request_id: saved.request_id, price_option_id: saved.price_option.id });
});

test("yearly saving is worked out from the server's monthly and yearly amounts", () => {
  const month = { id: "b1336f1f-6927-4680-8a6b-7b7c86a06e89", kind: "stripe" as const, interval: "month" as const, interval_count: 1 as const, amount_minor: 9900, currency: "usd", currency_exponent: 2 as const, active: true, checkout_available: true };
  const year = { ...month, id: "bd8cb7f8-78ab-4ac8-bb15-24d9de9d34b0", interval: "year" as const, amount_minor: 98600 };
  assert.equal(yearlySaving(year, [month, year]), "Equals $82.17 / month · save 17% vs monthly");
  assert.equal(yearlySaving(month, [month, year]), null);
  // No comparable monthly offer: no claim at all.
  assert.equal(yearlySaving(year, [year]), null);
  assert.equal(yearlySaving(year, [{ ...month, currency: "gbp" }, year]), null);
});

test("approvedCheckoutUrl accepts the two exact PayPal hosts and Stripe's checkout host", () => {
  assert.equal(approvedCheckoutUrl("https://www.paypal.com/checkoutnow?token=abc", "paypal"), "https://www.paypal.com/checkoutnow?token=abc");
  assert.equal(approvedCheckoutUrl("https://www.sandbox.paypal.com/checkoutnow?token=abc", "paypal"), "https://www.sandbox.paypal.com/checkoutnow?token=abc");
  assert.equal(approvedCheckoutUrl("https://checkout.stripe.com/c/pay/cs_1"), "https://checkout.stripe.com/c/pay/cs_1");
});

test("the paypal kind accepts the two PayPal hosts only and refuses Stripe's checkout", () => {
  assert.equal(approvedCheckoutUrl("https://www.paypal.com/checkoutnow?token=abc", "paypal"), "https://www.paypal.com/checkoutnow?token=abc");
  assert.equal(approvedCheckoutUrl("https://www.sandbox.paypal.com/checkoutnow?token=abc", "paypal"), "https://www.sandbox.paypal.com/checkoutnow?token=abc");
  assert.throws(() => approvedCheckoutUrl("https://checkout.stripe.com/c/pay/cs_1", "paypal"), /could not be verified/);
});

test("the checkout kind refuses the PayPal hosts and accepts Stripe's; portal is unchanged", () => {
  assert.throws(() => approvedCheckoutUrl("https://www.paypal.com/checkoutnow", "checkout"), /could not be verified/);
  assert.throws(() => approvedCheckoutUrl("https://www.sandbox.paypal.com/checkoutnow"), /could not be verified/);
  assert.equal(approvedCheckoutUrl("https://checkout.stripe.com/c/pay/cs_1", "checkout"), "https://checkout.stripe.com/c/pay/cs_1");
  assert.equal(approvedCheckoutUrl("https://billing.stripe.com/p/session/x", "portal"), "https://billing.stripe.com/p/session/x");
  assert.throws(() => approvedCheckoutUrl("https://checkout.stripe.com/c/pay/cs_1", "portal"), /could not be verified/);
});

for (const refused of [
  "http://www.paypal.com/checkoutnow",
  "https://paypal.com.evil.example",
  "https://www.paypal.com.evil.example",
  "https://evil.example/?u=https://www.paypal.com",
  "https://www.paypal.com@evil.example/",
  "https://user:pw@www.paypal.com/checkoutnow",
]) {
  test(`approvedCheckoutUrl refuses ${refused} under every kind`, () => {
    for (const kind of ["checkout", "paypal", "portal"] as const) assert.throws(() => approvedCheckoutUrl(refused, kind), /could not be verified/);
  });
}

test("a PayPal address is never accepted as the billing portal", () => {
  assert.throws(() => approvedCheckoutUrl("https://www.paypal.com/checkoutnow", "portal"), /could not be verified/);
});

const priceOption = { id: saved.price_option.id, kind: "stripe", interval: "month", interval_count: 1, amount_minor: 1250, currency: "usd", currency_exponent: 2, active: true, checkout_available: true };
const catalogue = (option: object) => [{ id: "starter", name: "Starter", minutes: 100, concurrent_calls: 1, billing_mode: "live", price_options: [option] }];
test("paypal_available defaults to false and parses when present", () => {
  assert.equal(billingCatalogSchema.parse(catalogue(priceOption))[0]!.price_options[0]!.paypal_available, false);
  assert.equal(billingCatalogSchema.parse(catalogue({ ...priceOption, paypal_available: true }))[0]!.price_options[0]!.paypal_available, true);
});
const subscriptionBody = { status: "active", minutes_allocated: 1, minutes_used: 0, minutes_remaining: 1, purchased_price_option: saved.price_option };
test("billing_provider defaults to null and parses when present", () => {
  assert.equal(billingSubscriptionSchema.parse(subscriptionBody).billing_provider, null);
  assert.equal(billingSubscriptionSchema.parse({ ...subscriptionBody, billing_provider: "paypal" }).billing_provider, "paypal");
  assert.equal(billingSubscriptionSchema.safeParse({ ...subscriptionBody, billing_provider: "other" }).success, false);
});
test("an active PayPal subscriber has a managed subscription; a cancelled or inactive one does not", () => {
  const paypal = (over: object) => billingSubscriptionSchema.parse({ ...subscriptionBody, billing_provider: "paypal", ...over });
  assert.equal(hasManagedSubscription(paypal({})), true);
  assert.equal(hasManagedSubscription(paypal({ cancel_at_period_end: true })), true);
  assert.equal(hasManagedSubscription(paypal({ purchased_price_option: null })), true);
  assert.equal(hasManagedSubscription(paypal({ status: "cancelled" })), false);
  assert.equal(hasManagedSubscription(paypal({ status: "inactive" })), false);
});
test("a saved purchase stores the provider only when it is PayPal", () => {
  assert.equal(savedPurchaseSchema.parse(saved).provider, undefined);
  assert.equal(savedPurchaseSchema.parse({ ...saved, provider: "paypal" }).provider, "paypal");
});
