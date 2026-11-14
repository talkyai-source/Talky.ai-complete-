import { describe, it } from "node:test";
import assert from "node:assert";
import { currencyForCountry, currencyName, formatEstimate, isUsableRate, usdFromMinor } from "./display-currency";

describe("display currency", () => {
  it("maps a country to its currency, whatever the letter case", () => {
    assert.equal(currencyForCountry("GB"), "GBP");
    assert.equal(currencyForCountry("pk"), "PKR");
    assert.equal(currencyForCountry(" de "), "EUR");
    assert.equal(currencyForCountry("FR"), "EUR");
    assert.equal(currencyForCountry("AE"), "AED");
    assert.equal(currencyForCountry("IN"), "INR");
  });

  it("gives no estimate for the United States, an unlisted country or a malformed value", () => {
    assert.equal(currencyForCountry("US"), null);
    assert.equal(currencyForCountry("ZZ"), null);
    assert.equal(currencyForCountry(""), null);
    assert.equal(currencyForCountry(null), null);
    assert.equal(currencyForCountry(undefined), null);
    assert.equal(currencyForCountry("GBR"), null);
    assert.equal(currencyForCountry("G1"), null);
    assert.equal(currencyForCountry("constructor"), null);
    assert.equal(currencyForCountry("__proto__"), null);
  });

  it("rounds a plan price to a whole amount and keeps two decimals for a per-minute rate", () => {
    assert.equal(formatEstimate(99, 0.755149, "GBP"), "£75");
    assert.equal(formatEstimate(0.4, 0.755149, "GBP"), "£0.30");
    assert.equal(formatEstimate(299, 0.903491, "EUR"), "€270");
    assert.match(formatEstimate(99, 276.622899, "PKR"), /^PKR\s27,386$/);
    assert.equal(formatEstimate(50, 159.795417, "JPY"), "¥7,990");
  });

  it("never writes a dollar sign that could be mistaken for US dollars", () => {
    assert.equal(formatEstimate(99, 1.4258, "CAD"), "CA$141");
    assert.equal(formatEstimate(99, 1.4738, "AUD"), "A$146");
  });

  it("accepts only a real, positive, plausible rate", () => {
    assert.equal(isUsableRate(0.75), true);
    assert.equal(isUsableRate(18245), true);
    for (const bad of [0, -1, Number.NaN, Number.POSITIVE_INFINITY, 1e9, "0.75", null, undefined]) {
      assert.equal(isUsableRate(bad), false, String(bad));
    }
  });

  it("names a currency in words", () => {
    assert.equal(currencyName("GBP"), "British Pound");
    assert.equal(currencyName("PKR"), "Pakistani Rupee");
  });

  it("gives the dollar value of a stored amount only when it is a usable dollar amount", () => {
    assert.equal(usdFromMinor(9900, "usd", 2), 99);
    assert.equal(usdFromMinor(9900, "USD", 2), 99);
    assert.equal(usdFromMinor(-5000, "usd", 2), -50);
    assert.equal(usdFromMinor(0, "usd", 2), 0);
    assert.equal(usdFromMinor(1250, "jpy", 0), null, "a yen amount is not converted from dollars");
    assert.equal(usdFromMinor(9900, "gbp", 2), null);
    const unusable: [number | null, string | null, number | null][] = [
      [null, "usd", 2], [9900, null, 2], [9900, "usd", null], [Number.NaN, "usd", 2], [9900, "usd", 1.5], [9900, "usd", -1],
    ];
    for (const [minor, currency, exponent] of unusable) {
      assert.equal(usdFromMinor(minor, currency, exponent), null, JSON.stringify([minor, currency, exponent]));
    }
  });

  it("rounds a large refund like a large charge, and writes zero without decimals", () => {
    const plain = (text: string) => text.replace(/ /g, " ");
    assert.equal(plain(formatEstimate(50, 276.6, "PKR")), "PKR 13,830");
    assert.equal(plain(formatEstimate(-50, 276.6, "PKR")), "-PKR 13,830");
    assert.equal(plain(formatEstimate(0, 276.6, "PKR")), "PKR 0");
    assert.equal(plain(formatEstimate(0.02, 276.6, "PKR")), "PKR 5.53");
    assert.equal(formatEstimate(-50, 0.755149, "GBP"), "-£38");
  });
});
