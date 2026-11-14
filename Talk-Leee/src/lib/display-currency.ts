// Local-currency price estimates for the public pricing page.
//
// Talk-Lee charges in US dollars. A visitor outside the US also sees an
// estimate in the currency of the country their connection comes from, so the
// price is readable at a glance. The dollar price stays on the page: it is the
// amount charged, and the estimate is labelled as an estimate.

/** The currency every price is charged in. */
export const BILLING_CURRENCY = "USD";

const EURO_COUNTRIES = [
  "AT", "BE", "BG", "HR", "CY", "EE", "FI", "FR", "DE", "GR", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PT", "SK", "SI", "ES",
  "AD", "MC", "SM", "VA", "ME", "XK",
];

// ISO 3166-1 alpha-2 country -> ISO 4217 currency. Every currency here is one
// the rate source carries (checked on 14 Nov 2026). A country that is not
// listed, and the United States, get no estimate: they see dollars only.
const COUNTRY_CURRENCY: Readonly<Record<string, string>> = {
  ...Object.fromEntries(EURO_COUNTRIES.map((country) => [country, "EUR"])),
  GB: "GBP", IM: "GBP", JE: "GBP", GG: "GBP",
  PK: "PKR", IN: "INR", BD: "BDT", LK: "LKR", NP: "NPR",
  AE: "AED", SA: "SAR", QA: "QAR", KW: "KWD", BH: "BHD", OM: "OMR", IL: "ILS", TR: "TRY", EG: "EGP",
  CA: "CAD", MX: "MXN", BR: "BRL",
  AU: "AUD", NZ: "NZD",
  SG: "SGD", MY: "MYR", ID: "IDR", PH: "PHP", TH: "THB", JP: "JPY", KR: "KRW", CN: "CNY", HK: "HKD",
  CH: "CHF", LI: "CHF", SE: "SEK", NO: "NOK", DK: "DKK", PL: "PLN", CZ: "CZK", HU: "HUF", RO: "RON",
  ZA: "ZAR", NG: "NGN", KE: "KES",
};

/** The currency to estimate in for a country code, or null for "dollars only". */
export function currencyForCountry(country: string | null | undefined): string | null {
  if (typeof country !== "string") return null;
  const code = country.trim().toUpperCase();
  if (!/^[A-Z]{2}$/.test(code)) return null;
  return COUNTRY_CURRENCY[code] ?? null;
}

/** A rate the page can safely multiply by: a real, positive, plausible number. */
export function isUsableRate(rate: unknown): rate is number {
  return typeof rate === "number" && Number.isFinite(rate) && rate > 0 && rate < 1_000_000;
}

/**
 * A dollar amount in another currency, for display. Amounts under 10 keep two
 * decimals (a per-minute rate); larger ones are rounded to a whole number,
 * because an estimate to the penny would claim a precision it does not have.
 */
export function formatEstimate(usd: number, rate: number, currency: string): string {
  const amount = usd * rate;
  // By size, not sign: a refund of -5,000 is a large amount too. Zero needs no decimals.
  const digits = amount !== 0 && Math.abs(amount) < 10 ? 2 : 0;
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(amount);
  } catch {
    return `${currency} ${amount.toFixed(digits)}`;
  }
}

/**
 * The dollar value of a stored amount in minor units (cents), or null when the
 * amount is missing or is not a US-dollar amount. Only a dollar amount can be
 * estimated, because the rates are quoted against the dollar.
 */
export function usdFromMinor(
  minor: number | null | undefined,
  currency: string | null | undefined,
  exponent: number | null | undefined,
): number | null {
  if (typeof minor !== "number" || !Number.isFinite(minor)) return null;
  if (typeof currency !== "string" || currency.toUpperCase() !== BILLING_CURRENCY) return null;
  if (typeof exponent !== "number" || !Number.isInteger(exponent) || exponent < 0 || exponent > 4) return null;
  return minor / 10 ** exponent;
}

/** "British Pound" for "GBP"; the code itself when the browser has no name for it. */
export function currencyName(currency: string): string {
  try {
    return new Intl.DisplayNames(["en"], { type: "currency" }).of(currency) ?? currency;
  } catch {
    return currency;
  }
}
