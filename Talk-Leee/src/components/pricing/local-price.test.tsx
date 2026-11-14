import { describe, it, afterEach } from "node:test";
import assert from "node:assert";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import React from "react";
import { DisplayCurrencyProvider, LocalFirstPrice, LocalEstimateNote, loadDisplayCurrency } from "./local-price";
import { PayAsYouGoCard, type PaygBundle } from "@/app/pricing/pay-as-you-go";

const page = () =>
  render(
    <DisplayCurrencyProvider>
      <LocalFirstPrice usd={99} usdText="$99/month" suffix="/month" className="headline" chargedClassName="charged" />
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

  it("shows the visitor's own currency as the main price, with the dollar charge under it", async () => {
    answerWith({ currency: "GBP", rate: 0.755149, as_of: "2026-11-13" });
    page();
    await waitFor(() => assert.equal(screen.getByTestId("local-estimate").textContent, "≈ £75/month"));
    // The local amount takes the headline's place and style; the dollar amount
    // that is charged moves directly under it, in the smaller style.
    const local = screen.getByTestId("local-estimate");
    assert.equal(local.className, "headline");
    const charged = screen.getByTestId("charged-in-usd");
    assert.equal(charged.textContent, "$99/month charged in US dollars");
    assert.equal(charged.className, "charged");
    assert.equal(charged.previousElementSibling, local);
    assert.equal(screen.queryByText("$99/month"), null, "the dollar price is no longer a headline of its own");
    const note = screen.getByTestId("local-estimate-note").textContent ?? "";
    assert.match(note, /charged in US dollars/);
    assert.match(note, /GBP \(British Pound\) are estimates at the exchange rate of 13 Nov 2026; the amount your bank charges may differ/);
    assert.equal(screen.getByRole("link", { name: "Rates By Exchange Rate API" }).getAttribute("href"), "https://www.exchangerate-api.com");
    assert.deepEqual(asked, ["/api/display-currency"]);
  });

  it("shows the dollar price alone for a visitor who is shown dollars only", async () => {
    answerWith({ currency: null });
    page();
    // Before the lookup answers (and in the server HTML) the dollar price is the headline.
    assert.equal(screen.getByText("$99/month").className, "headline");
    await waitFor(() => assert.equal(asked.length, 1));
    assert.equal(screen.queryByTestId("local-estimate"), null);
    assert.equal(screen.queryByTestId("charged-in-usd"), null);
    assert.equal(screen.queryByTestId("local-estimate-note"), null);
    assert.equal(screen.getByText("$99/month").className, "headline");
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

  it("leads the live pay-as-you-go bundles with the local amount, but only when they are priced in dollars", async () => {
    const bundle: PaygBundle = { code: "payg_125", minutes: 125, price_cents: 5000, currency: "usd", currency_exponent: 2 };
    answerWith({ currency: "GBP", rate: 0.755149, as_of: "2026-11-13" });
    render(
      <DisplayCurrencyProvider>
        <PayAsYouGoCard bundles={[bundle, { ...bundle, code: "payg_eur", currency: "eur" }]} />
      </DisplayCurrencyProvider>,
    );
    await waitFor(() => assert.equal(screen.getAllByTestId("local-estimate").length, 1));
    assert.equal(screen.getByTestId("local-estimate").textContent, "≈ £38");
    const lines = screen.getAllByRole("listitem").map((item) => (item.textContent ?? "").replace(/\s+/g, " ").trim());
    assert.deepEqual(lines, [
      "• 125 minutes: ≈ £38 ($50.00 charged in US dollars)",
      "• 125 minutes: €50.00",
    ]);
  });
});
