// The figures the pricing page shows, in one place. Money is held in cents.
// The truth is the backend: catalog-parity.test.ts reads these files at run
// time and fails when a figure here differs from them.
//   prices:  backend/scripts/stripe_catalog_setup.py  (CATALOG)
//   minutes, calls at once, campaigns, top-up bundles:
//            backend/Alembic/versions/0071_paid_only_billing.py
//   pay as you go: backend/Alembic/versions/0083_pay_as_you_go.py
// A Next page file may not export extra names, so the constant lives here.

export type CatalogPlan = {
  id: "basic" | "business";
  name: string;
  monthlyCents: number;
  annualCents: number;
  minutes: number;
  concurrentCalls: number;
  campaigns: number;
  storageGb: number;
};

export const CATALOG_PLANS: CatalogPlan[] = [
  { id: "basic", name: "Basic", monthlyCents: 9900, annualCents: 98600, minutes: 300, concurrentCalls: 3, campaigns: 2, storageGb: 2 },
  { id: "business", name: "Business", monthlyCents: 29900, annualCents: 297800, minutes: 1000, concurrentCalls: 10, campaigns: 10, storageGb: 4 },
];

export const CATALOG_TOPUPS: { priceCents: number; minutes: number }[] = [
  { priceCents: 5000, minutes: 125 },
  { priceCents: 7000, minutes: 175 },
  { priceCents: 10000, minutes: 250 },
];

// The only figure on the page with no backend row: ElevenLabs is not sold or
// metered by the product (owner decision O19: keep the line and the figure).
export const ELEVENLABS_MONTHLY_USD = 50;

/** "$99", "$2,978" for whole dollars, "$82.17" otherwise. */
export function formatUsd(cents: number): string {
  const whole = cents % 100 === 0;
  return `$${(cents / 100).toLocaleString("en-US", {
    minimumFractionDigits: whole ? 0 : 2,
    maximumFractionDigits: whole ? 0 : 2,
  })}`;
}

/** The yearly total spread over twelve months, to the cent (98600 -> 8217). */
export function annualPerMonthCents(annualCents: number): number {
  return Math.round(annualCents / 12);
}

/** How much cheaper twelve months on the yearly price are than twelve monthly payments, in whole percent. */
export function annualSavingPercent(plan: Pick<CatalogPlan, "monthlyCents" | "annualCents">): number {
  return Math.round((1 - plan.annualCents / (plan.monthlyCents * 12)) * 100);
}

/** One saving for the page; the switch and the cards state it, so the plans must agree. */
export function pageSavingPercent(plans: CatalogPlan[] = CATALOG_PLANS): number {
  const percents = new Set(plans.map(annualSavingPercent));
  if (percents.size !== 1) throw new Error("The plans do not share one annual saving");
  return [...percents][0];
}

/** "$0.40" per minute of a bundle. */
export function bundleRateUsd(bundle: { priceCents: number; minutes: number }): number {
  return Math.round((bundle.priceCents / bundle.minutes)) / 100;
}
