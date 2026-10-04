import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { act, cleanup, screen } from "@testing-library/react";
import type { QueryClient } from "@tanstack/react-query";
import { SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import { TopupCard } from "@/components/billing/topup-card";
import { topupKeys, type TopupOrder } from "@/lib/topup-api";
import { renderWithQueryClient } from "@/test-utils/render";

const originalFetch = globalThis.fetch;
const clients: QueryClient[] = [];
const balance = { allocated: 1000, used_minutes: 20, remaining_minutes: 980, unlimited: false, exhausted: false, purchased_minutes: 250 };
const order: TopupOrder = { id: "00000000-0000-4000-8000-000000000404", package_code: "synthetic", minutes: 250, price_cents: 2500, currency: "gbp", currency_exponent: 2,
  status: "paid", created_at: "2026-10-01T00:00:00Z", paid_at: "2026-10-01T00:00:01Z", provider_payment_id: "pi_synthetic",
  refund_details: { detail_status: "unavailable", captured_at: null, source_event_id: null, refunds: null } };
function setup(query: string, orders: unknown[] = [], failure?: string) {
  globalThis.fetch = async (input) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const path = new URL(url).pathname;
    const bad = failure && path.endsWith(failure);
    const data = bad ? { detail: "Synthetic unavailable" } : path.endsWith("/balance") ? balance : path.endsWith("/packages") ? [] : { orders };
    return new Response(JSON.stringify(data), { status: bad ? 503 : 200, headers: { "content-type": "application/json" } });
  };
  const result = renderWithQueryClient(<SearchParamsContext.Provider value={new URLSearchParams(query)}><TopupCard /></SearchParamsContext.Provider>);
  clients.push(result.qc); return result;
}
afterEach(() => { cleanup(); globalThis.fetch = originalFetch; clients.splice(0).forEach((client) => client.clear()); });

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
