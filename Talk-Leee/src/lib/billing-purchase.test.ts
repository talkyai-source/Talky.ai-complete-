import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { api } from "./api";
import { env } from "./env";
import { formatPurchasedPrice, startBillingCheckout, type SavedPurchase } from "./billing-purchase";
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
