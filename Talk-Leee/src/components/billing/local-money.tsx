"use client";

import { LocalAmount } from "@/components/pricing/local-price";
import { usdFromMinor } from "@/lib/display-currency";
import { formatMinorMoney } from "@/lib/billing-read";
import { formatPurchasedPrice, yearlySavingParts, type BillingPrice, type PurchasedPrice } from "@/lib/billing-purchase";

/**
 * A recorded amount in minor units (cents). A visitor outside the United
 * States reads their own currency first, as an estimate, with the recorded
 * dollar amount in brackets. An amount that is missing or not in dollars is
 * shown exactly as before.
 */
export function LocalMinorAmount({ minor, currency, exponent }: {
  minor: number | null;
  currency: string | null;
  exponent: number | null;
}) {
  return <LocalAmount usd={usdFromMinor(minor, currency, exponent)} usdText={formatMinorMoney(minor, currency, exponent)} />;
}

/** A plan price such as "$99.00 / month", the visitor's currency first. */
export function LocalPurchasedPrice({ price }: { price: PurchasedPrice | BillingPrice }) {
  const text = formatPurchasedPrice(price);
  if (price.kind === "free") return <>{text}</>;
  return (
    <LocalAmount
      usd={usdFromMinor(price.amount_minor, price.currency, price.currency_exponent)}
      usdText={text}
      suffix={` / ${price.interval}`}
    />
  );
}

/** "Equals $82.17 / month · save 17% vs monthly", the visitor's currency first. Nothing when there is no saving line. */
export function LocalYearlySaving({ year, options }: { year: BillingPrice; options: readonly BillingPrice[] }) {
  const parts = yearlySavingParts(year, options);
  if (!parts) return null;
  return (
    <>
      Equals{" "}
      <LocalAmount
        usd={usdFromMinor(parts.perMonthMinor, parts.currency, parts.exponent)}
        usdText={`${parts.perMonth} / month`}
        suffix=" / month"
      />
      {parts.percent > 0 ? ` · save ${parts.percent}% vs monthly` : ""}
    </>
  );
}
