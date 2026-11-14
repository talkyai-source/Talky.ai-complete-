"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { z } from "zod";
import { BILLING_CURRENCY, currencyName, formatEstimate, isUsableRate } from "@/lib/display-currency";

export type DisplayCurrency = { currency: string; rate: number; asOf: string | null };

const answerSchema = z.object({
  currency: z.string().length(3).nullable(),
  rate: z.number().optional(),
  as_of: z.string().optional(),
});

/** Asks the site which currency to estimate in. Null means "show dollars only". */
export async function loadDisplayCurrency(fetchImpl: typeof fetch = fetch): Promise<DisplayCurrency | null> {
  try {
    // A team member can preview another country with /pricing/?country=GB.
    const preview = new URLSearchParams(window.location.search).get("country");
    const query = preview && /^[A-Za-z]{2}$/.test(preview) ? `?country=${preview.toUpperCase()}` : "";
    // Default credentials on purpose: a protected preview deployment only
    // answers a request that carries its own access cookie. The route reads no cookie.
    const response = await fetchImpl(`/api/display-currency${query}`);
    if (!response.ok) return null;
    const answer = answerSchema.parse(await response.json());
    if (!answer.currency || answer.currency === BILLING_CURRENCY || !isUsableRate(answer.rate)) return null;
    return { currency: answer.currency, rate: answer.rate, asOf: answer.as_of ?? null };
  } catch {
    return null;
  }
}

const DisplayCurrencyContext = createContext<DisplayCurrency | null>(null);

/** Loads the visitor's display currency once for everything inside it. */
export function DisplayCurrencyProvider({ children }: { children: ReactNode }) {
  const [value, setValue] = useState<DisplayCurrency | null>(null);
  useEffect(() => {
    let live = true;
    void loadDisplayCurrency().then((loaded) => { if (live) setValue(loaded); });
    return () => { live = false; };
  }, []);
  return <DisplayCurrencyContext.Provider value={value}>{children}</DisplayCurrencyContext.Provider>;
}

/** The visitor's display currency, or null when they are shown dollars only. */
export function useDisplayCurrency(): DisplayCurrency | null {
  return useContext(DisplayCurrencyContext);
}

/**
 * One price, in the visitor's own currency first. A visitor who is shown
 * dollars only gets the dollar price alone, so the page is unchanged for them
 * (and in the server HTML). Anyone else gets the local estimate in the
 * headline's place, for example "≈ £75/month", and the dollar amount that is
 * actually charged directly under it.
 */
export function LocalFirstPrice({ usd, usdText, suffix = "", className, chargedClassName }: {
  usd: number;
  usdText: string;
  suffix?: string;
  className?: string;
  chargedClassName?: string;
}) {
  const display = useDisplayCurrency();
  if (!display) return <p className={className}>{usdText}</p>;
  return (
    <>
      <p className={className} data-testid="local-estimate">
        ≈ {formatEstimate(usd, display.rate, display.currency)}{suffix}
      </p>
      <p className={chargedClassName} data-testid="charged-in-usd">{usdText} charged in US dollars</p>
    </>
  );
}

/** "13 Nov 2026" for "2026-11-13"; null when the date is missing or malformed. */
function readableDate(isoDate: string | null): string | null {
  if (!isoDate || !/^\d{4}-\d{2}-\d{2}$/.test(isoDate)) return null;
  const date = new Date(`${isoDate}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }).format(date);
}

/** Says, once per page, that the local amounts are estimates and what is charged. */
export function LocalEstimateNote({ className }: { className?: string }) {
  const display = useContext(DisplayCurrencyContext);
  if (!display) return null;
  const rateDate = readableDate(display.asOf);
  return (
    <p className={className} data-testid="local-estimate-note">
      Prices are charged in US dollars. The amounts shown in {display.currency} ({currencyName(display.currency)}) are
      estimates{rateDate ? <> at the exchange rate of <span className="whitespace-nowrap">{rateDate}</span></> : null}; the
      amount your bank charges may differ.{" "}
      <a
        href="https://www.exchangerate-api.com"
        target="_blank"
        rel="noopener noreferrer"
        className="underline underline-offset-4"
      >
        Rates By Exchange Rate API
      </a>
    </p>
  );
}
