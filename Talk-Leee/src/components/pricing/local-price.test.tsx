import { describe, it, afterEach } from "node:test";
import assert from "node:assert";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import React from "react";
import { DisplayCurrencyProvider, LocalEstimate, LocalEstimateNote, loadDisplayCurrency } from "./local-price";
import { PayAsYouGoCard, type PaygBundle } from "@/app/pricing/pay-as-you-go";

const page = () =>
  render(
    <DisplayCurrencyProvider>
      <p>$99/month</p>
      <LocalEstimate usd={99} suffix="/month" />
      <LocalEstimateNote />
    </DisplayCurrencyProvider>,
  );

describe("local price estimates", () => {
  const originalFetch = globalThis.fetch;
  let asked: string[] = [];
  const answerWith = (body: unknown, status = 200) => {
    asked = [];
    globalThis.fetch = async (input) => { asked.push(String(input)); return Response.json(body, { status }); };
  };
  afterEach(() => {
    cleanup();
    globalThis.fetch = originalFetch;
    window.history.replaceState({}, "", "/");
  });

  it("shows an estimate beside the dollar price, and says what is charged", async () => {
    answerWith({ currency: "GBP", rate: 0.755149, as_of: "2026-11-13" });
    page();
    await waitFor(() => assert.equal(screen.getByTestId("local-estimate").textContent, "≈ £75/month"));
    assert.ok(screen.getByText("$99/month"));
    const note = screen.getByTestId("local-estimate-note").textContent ?? "";
    assert.match(note, /charged in US dollars/);
    assert.match(note, /GBP \(British Pound\) are estimates at the exchange rate of 13 Nov 2026; the amount your bank charges may differ/);
    assert.equal(screen.getByRole("link", { name: "Rates By Exchange Rate API" }).getAttribute("href"), "https://www.exchangerate-api.com");
    assert.deepEqual(asked, ["/api/display-currency"]);
  });

  it("adds nothing for a visitor who is shown dollars only", async () => {
    answerWith({ currency: null });
    page();
    await waitFor(() => assert.equal(asked.length, 1));
    assert.equal(screen.queryByTestId("local-estimate"), null);
    assert.equal(screen.queryByTestId("local-estimate-note"), null);
    assert.ok(screen.getByText("$99/month"));
  });

  it("adds nothing when the lookup fails, answers oddly, or names dollars", async () => {
    answerWith({ error: "unavailable" }, 503);
    assert.equal(await loadDisplayCurrency(), null);
    answerWith({ currency: "GBP" });
    assert.equal(await loadDisplayCurrency(), null);
    answerWith({ currency: "GBP", rate: 0 });
    assert.equal(await loadDisplayCurrency(), null);
    answerWith({ currency: "USD", rate: 1 });
    assert.equal(await loadDisplayCurrency(), null);
    answerWith({ currency: "POUNDS", rate: 0.75 });
    assert.equal(await loadDisplayCurrency(), null);
    globalThis.fetch = async () => { throw new Error("offline"); };
    assert.equal(await loadDisplayCurrency(), null);
  });

  it("passes a ?country= preview through, and ignores a malformed one", async () => {
    answerWith({ currency: "PKR", rate: 276.62, as_of: "2026-11-13" });
    window.history.replaceState({}, "", "/pricing/?country=pk");
    assert.deepEqual(await loadDisplayCurrency(), { currency: "PKR", rate: 276.62, asOf: "2026-11-13" });
    assert.deepEqual(asked, ["/api/display-currency?country=PK"]);
    answerWith({ currency: null });
    window.history.replaceState({}, "", "/pricing/?country=pk%26x=1");
    await loadDisplayCurrency();
    assert.deepEqual(asked, ["/api/display-currency"]);
  });

  it("estimates the live pay-as-you-go bundles, but only when they are priced in dollars", async () => {
    const bundle: PaygBundle = { code: "payg_125", minutes: 125, price_cents: 5000, currency: "usd", currency_exponent: 2 };
    answerWith({ currency: "GBP", rate: 0.755149, as_of: "2026-11-13" });
    render(
      <DisplayCurrencyProvider>
        <PayAsYouGoCard bundles={[bundle, { ...bundle, code: "payg_eur", currency: "eur" }]} />
      </DisplayCurrencyProvider>,
    );
    await waitFor(() => assert.equal(screen.getAllByTestId("local-estimate").length, 1));
    assert.equal(screen.getByTestId("local-estimate").textContent, "≈ £38");
    assert.ok(screen.getByText(/125 minutes: \$50\.00/));
  });
});
