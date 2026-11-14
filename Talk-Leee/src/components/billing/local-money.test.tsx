import { describe, it, afterEach } from "node:test";
import assert from "node:assert";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import React from "react";
import { DisplayCurrencyProvider } from "@/components/pricing/local-price";
import type { BillingPrice } from "@/lib/billing-purchase";
import { LocalMinorAmount, LocalPurchasedPrice, LocalYearlySaving } from "./local-money";

const month: BillingPrice = {
  id: "11111111-1111-4111-8111-111111111111", kind: "stripe", interval: "month", interval_count: 1,
  amount_minor: 9900, currency: "usd", currency_exponent: 2, active: true, checkout_available: true,
};
const year: BillingPrice = { ...month, id: "22222222-2222-4222-8222-222222222222", interval: "year", amount_minor: 98600 };
const free: BillingPrice = { ...month, id: "33333333-3333-4333-8333-333333333333", kind: "free", amount_minor: 0 };

// Every money line the signed-in Billing pages print, one per test id.
const billingLines = () =>
  render(
    <DisplayCurrencyProvider>
      <p data-testid="plan"><LocalPurchasedPrice price={month} /></p>
      <p data-testid="free"><LocalPurchasedPrice price={free} /></p>
      <p data-testid="saving"><LocalYearlySaving year={year} options={[month, year]} /></p>
      <p data-testid="no-saving"><LocalYearlySaving year={month} options={[month, year]} /></p>
      <p data-testid="invoice"><LocalMinorAmount minor={3000} currency="usd" exponent={2} /></p>
      <p data-testid="refund"><LocalMinorAmount minor={-5000} currency="usd" exponent={2} /></p>
      <p data-testid="nothing-due"><LocalMinorAmount minor={0} currency="usd" exponent={2} /></p>
      <p data-testid="pounds"><LocalMinorAmount minor={3000} currency="gbp" exponent={2} /></p>
      <p data-testid="missing"><LocalMinorAmount minor={null} currency="usd" exponent={2} /></p>
      <p data-testid="no-precision"><LocalMinorAmount minor={3000} currency="usd" exponent={null} /></p>
    </DisplayCurrencyProvider>,
  );
const line = (id: string) => screen.getByTestId(id).textContent;

describe("billing amounts in the visitor's currency", () => {
  const originalFetch = globalThis.fetch;
  let asked: string[] = [];
  const answerWith = (body: unknown) => {
    asked = [];
    globalThis.fetch = async (input) => { asked.push(String(input)); return Response.json(body); };
  };
  afterEach(() => { cleanup(); globalThis.fetch = originalFetch; });

  it("puts the visitor's currency first and keeps the recorded dollar amount in brackets", async () => {
    answerWith({ currency: "GBP", rate: 0.755149, as_of: "2026-11-13" });
    billingLines();
    await waitFor(() => assert.equal(line("plan"), "≈ £75 / month ($99.00 / month)"));
    assert.equal(line("free"), "Free");
    assert.equal(line("saving"), "Equals ≈ £62 / month ($82.17 / month) · save 17% vs monthly");
    assert.equal(line("no-saving"), "", "a monthly price has no saving line");
    assert.equal(line("invoice"), "≈ £23 ($30.00)");
    assert.equal(line("refund"), "≈ -£38 (-$50.00)");
    assert.equal(line("nothing-due"), "£0");
    assert.deepEqual(asked, ["/api/display-currency"], "one lookup for the whole page");
  });

  it("never estimates an amount that is not a known dollar amount", async () => {
    answerWith({ currency: "GBP", rate: 0.755149, as_of: "2026-11-13" });
    billingLines();
    await waitFor(() => assert.equal(line("invoice"), "≈ £23 ($30.00)"));
    assert.equal(line("pounds"), "£30.00", "a pound amount is already in pounds");
    assert.equal(line("missing"), "Unavailable");
    assert.equal(line("no-precision"), "Unavailable");
  });

  it("is unchanged for a visitor who is shown dollars only", async () => {
    answerWith({ currency: null });
    billingLines();
    await waitFor(() => assert.equal(asked.length, 1));
    assert.equal(line("plan"), "$99.00 / month");
    assert.equal(line("free"), "Free");
    assert.equal(line("saving"), "Equals $82.17 / month · save 17% vs monthly");
    assert.equal(line("invoice"), "$30.00");
    assert.equal(line("refund"), "-$50.00");
    assert.equal(line("nothing-due"), "$0.00");
    assert.equal(line("pounds"), "£30.00");
    assert.equal(line("missing"), "Unavailable");
  });
});
