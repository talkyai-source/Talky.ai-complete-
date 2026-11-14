import { describe, it, afterEach } from "node:test";
import assert from "node:assert";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import React from "react";
import { DisplayCurrencyProvider, LocalAmount, LocalFirstPrice, LocalEstimateNote, loadDisplayCurrency, localAmountText } from "./local-price";
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

  it("shows a dollar amount in running text in the visitor's currency first, with the dollars in brackets", async () => {
    answerWith({ currency: "GBP", rate: 0.755149, as_of: "2026-11-13" });
    render(
      <DisplayCurrencyProvider>
        <p data-testid="line">Monthly price: <LocalAmount usd={99} usdText="$99/month" suffix="/month" /></p>
        <p data-testid="zero"><LocalAmount usd={0} usdText="$0" /></p>
        <p data-testid="other"><LocalAmount usd={null} usdText="€50.00" /></p>
        <p data-testid="long"><LocalAmount usd={986} usdText="$986/year" usdOnlyText="$986/year ($82/month)" suffix="/year" /></p>
      </DisplayCurrencyProvider>,
    );
    await waitFor(() => assert.equal(screen.getByTestId("line").textContent, "Monthly price: ≈ £75/month ($99/month)"));
    assert.equal(screen.getByTestId("zero").textContent, "£0", "zero is zero in every currency");
    assert.equal(screen.getByTestId("other").textContent, "€50.00", "an amount that is not in dollars is left alone");
    assert.equal(screen.getByTestId("long").textContent, "≈ £745/year ($986/year)");
    assert.deepEqual(asked, ["/api/display-currency"], "one lookup for the whole page");
  });

  it("leaves a dollar amount exactly as written for a visitor who is shown dollars only", async () => {
    answerWith({ currency: null });
    render(
      <DisplayCurrencyProvider>
        <p data-testid="line"><LocalAmount usd={99} usdText="$99/month" suffix="/month" /></p>
        <p data-testid="long"><LocalAmount usd={986} usdText="$986/year" usdOnlyText="$986/year ($82/month)" suffix="/year" /></p>
      </DisplayCurrencyProvider>,
    );
    await waitFor(() => assert.equal(asked.length, 1));
    assert.equal(screen.getByTestId("line").textContent, "$99/month");
    assert.equal(screen.getByTestId("long").textContent, "$986/year ($82/month)");
    assert.equal(screen.queryByTestId("local-estimate"), null);
  });

  it("asks for nothing on a page that shows no money amount, or outside a provider", async () => {
    answerWith({ currency: "GBP", rate: 0.755149 });
    render(<DisplayCurrencyProvider><p>No prices on this page</p></DisplayCurrencyProvider>);
    render(<p data-testid="bare"><LocalAmount usd={99} usdText="$99" /></p>);
    await new Promise((resolve) => setTimeout(resolve, 25));
    assert.deepEqual(asked, []);
    assert.equal(screen.getByTestId("bare").textContent, "$99");
  });

  it("writes the same thing as plain text, for labels a screen reader announces", () => {
    const pounds = { currency: "GBP", rate: 0.755149, asOf: null };
    assert.equal(localAmountText(pounds, 50, "$50.00"), "≈ £38 ($50.00)");
    assert.equal(localAmountText(pounds, 50, "$50.00 / month", " / month"), "≈ £38 / month ($50.00 / month)");
    assert.equal(localAmountText(pounds, 0, "$0.00"), "£0");
    assert.equal(localAmountText(pounds, null, "Unavailable"), "Unavailable");
    assert.equal(localAmountText(null, 50, "$50.00"), "$50.00");
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
