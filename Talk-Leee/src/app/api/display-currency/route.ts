import { z } from "zod";
import { currencyForCountry, isUsableRate } from "@/lib/display-currency";

// Tells the pricing page which currency to show an estimate in, and at what
// rate. The country comes from the hosting platform's own header
// (x-vercel-ip-country), which it sets from the visitor's IP address; nothing
// about the visitor is stored here or sent anywhere. The rate request below
// carries no visitor data: it is the same request for everyone.
//
// Any failure (unknown country, rate source down, odd answer) returns
// `currency: null`, and the page simply shows its US dollar prices.

const RATES_URL = "https://open.er-api.com/v6/latest/USD";
// The source publishes once a day; six hours keeps the page close to it without
// asking more often than it needs to.
const RATES_MAX_AGE_SECONDS = 6 * 60 * 60;

const ratesSchema = z.object({
  result: z.literal("success"),
  base_code: z.literal("USD"),
  time_last_update_unix: z.number().int().positive(),
  rates: z.record(z.string(), z.number()),
});

const dollarsOnly = () =>
  Response.json({ currency: null }, { headers: { "Cache-Control": "private, no-store" } });

export async function GET(request: Request): Promise<Response> {
  // `?country=GB` lets the team preview what a visitor from that country sees.
  // It changes a displayed estimate only, never a price or a charge.
  const preview = new URL(request.url).searchParams.get("country");
  const country = preview ?? request.headers.get("x-vercel-ip-country");
  const currency = currencyForCountry(country);
  if (!currency) return dollarsOnly();
  try {
    const upstream = await fetch(RATES_URL, {
      next: { revalidate: RATES_MAX_AGE_SECONDS },
      signal: AbortSignal.timeout(5_000),
    });
    if (!upstream.ok) return dollarsOnly();
    const parsed = ratesSchema.safeParse(await upstream.json());
    if (!parsed.success) return dollarsOnly();
    const rate = parsed.data.rates[currency];
    if (!isUsableRate(rate)) return dollarsOnly();
    return Response.json(
      { currency, rate, as_of: new Date(parsed.data.time_last_update_unix * 1000).toISOString().slice(0, 10) },
      { headers: { "Cache-Control": "private, max-age=3600" } },
    );
  } catch {
    return dollarsOnly();
  }
}
