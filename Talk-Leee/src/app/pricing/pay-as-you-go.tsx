"use client";

import { useEffect, useState, type CSSProperties } from "react";
import { z } from "zod";
import { apiBaseUrl } from "@/lib/env";

// Pay-as-you-go prices live in the backend and change without a release, so the
// page reads them instead of hard-coding them.
const bundleSchema = z.object({
  code: z.string(),
  minutes: z.number().int().positive(),
  price_cents: z.number().int().positive(),
  currency: z.string().length(3),
  currency_exponent: z.number().int().nonnegative().nullable(),
});
const paygSchema = z.object({ packages: z.array(bundleSchema) });
export type PaygBundle = z.infer<typeof bundleSchema>;

export async function loadPayAsYouGo(fetchImpl: typeof fetch = fetch): Promise<PaygBundle[]> {
  try {
    const response = await fetchImpl(`${apiBaseUrl()}/plans/payg`);
    if (!response.ok) return [];
    return paygSchema.parse(await response.json()).packages;
  } catch {
    return [];
  }
}

export function formatBundlePrice(bundle: PaygBundle): string {
  const exponent = bundle.currency_exponent ?? 2;
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: bundle.currency.toUpperCase(),
    minimumFractionDigits: exponent,
    maximumFractionDigits: exponent,
  }).format(bundle.price_cents / 10 ** exponent);
}

type CardStyle = { className?: string; style?: CSSProperties; titleClassName?: string; bodyClassName?: string };

export function PayAsYouGoSection(props: CardStyle) {
  const [bundles, setBundles] = useState<PaygBundle[] | null>(null);
  useEffect(() => {
    let live = true;
    void loadPayAsYouGo().then((loaded) => { if (live) setBundles(loaded); });
    return () => { live = false; };
  }, []);
  return <PayAsYouGoCard bundles={bundles} {...props} />;
}

export function PayAsYouGoCard({ bundles, className, style, titleClassName, bodyClassName }: {
  bundles: PaygBundle[] | null;
  className?: string;
  style?: CSSProperties;
  titleClassName?: string;
  bodyClassName?: string;
}) {
  return (
    <div className={className} style={style}>
      <h2 className={titleClassName}>Pay as you go</h2>
      <p className="mt-3 text-2xl md:text-3xl font-bold tracking-tight text-primary dark:text-foreground">No monthly fee</p>
      <p className={bodyClassName}>
        Buy AI minutes when you need them. Minutes carry over until used, with every Business feature included.
      </p>
      {bundles === null ? (
        <p className={bodyClassName}>Loading current prices…</p>
      ) : bundles.length > 0 ? (
        <ul className="mt-4 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground" aria-label="Pay-as-you-go bundles">
          {bundles.map((bundle) => (
            <li key={bundle.code}>
              &bull; {bundle.minutes.toLocaleString("en-US")} minutes: {formatBundlePrice(bundle)}
            </li>
          ))}
        </ul>
      ) : (
        <p className={bodyClassName}>Current pay-as-you-go prices are shown in Billing after you sign up.</p>
      )}
    </div>
  );
}
