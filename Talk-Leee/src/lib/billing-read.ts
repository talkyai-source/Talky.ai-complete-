import { z } from "zod";

export const minorAmount = z.number().int().safe().nullable();
export const currencyCode = z.string().regex(/^[a-zA-Z]{3}$/).nullable();
export const currencyExponent = z.union([z.literal(0), z.literal(2), z.literal(3)]).nullable();
const date = z.string().datetime({ offset: true }).nullable();
const text = z.string().nullable();
export const providerDocumentUrl = z.string().url().refine((value) => {
  const url = new URL(value);
  return url.protocol === "https:" && !url.username && !url.password && !url.port &&
    ["invoice.stripe.com", "pay.stripe.com", "files.stripe.com"].includes(url.hostname);
}, "Unverified billing document link").nullable();

/** Format exact minor integers without a floating-point division losing cents. */
export function formatMinorMoney(amount: number | null, currency: string | null, exponent: number | null): string {
  if (amount === null || !Number.isSafeInteger(amount) || !currency || !/^[a-z]{3}$/i.test(currency) || ![0, 2, 3].includes(exponent ?? -1)) return "Unavailable";
  try {
    const places = exponent as number;
    const minor = BigInt(amount);
    const zero = BigInt(0);
    const scale = BigInt(10) ** BigInt(places);
    const absolute = minor < zero ? -minor : minor;
    const whole = absolute / scale;
    const signedWhole = minor < zero ? (whole === zero ? -0 : -whole) : whole;
    const fraction = (absolute % scale).toString().padStart(places, "0");
    return new Intl.NumberFormat("en-US", { style: "currency", currency: currency.toUpperCase(),
      minimumFractionDigits: places, maximumFractionDigits: places }).formatToParts(signedWhole)
      .map((part) => part.type === "fraction" ? fraction : part.value).join("");
  } catch { return "Unavailable"; }
}

export const invoiceLineSchema = z.object({
  id: z.string(), description: text, quantity: z.number().int().safe().nullable(), amount: minorAmount,
  currency: currencyCode, period_start: date, period_end: date,
});
export const invoiceSchema = z.object({
  id: z.string().uuid(), stripe_invoice_id: text, invoice_number: text.default(null), status: z.string(),
  currency: currencyCode, currency_exponent: currencyExponent,
  subtotal: minorAmount, total: minorAmount, amount_due: minorAmount, amount_paid: minorAmount, amount_remaining: minorAmount,
  period_start: date, period_end: date, paid_at: date, due_date: date, created_at: date,
  invoice_pdf: providerDocumentUrl, hosted_invoice_url: providerDocumentUrl,
  detail_status: z.enum(["complete", "partial", "unavailable"]),
  detail_source: z.enum(["provider_snapshot", "stored_summary"]), captured_at: date,
  source_reference: text.default(null),
  line_items: z.array(invoiceLineSchema).nullable(),
  taxes: z.array(z.object({ amount: minorAmount, inclusive: z.boolean().nullable(), tax_rate_id: text })).nullable(),
  discounts: z.array(z.object({ amount: minorAmount, discount_id: text })).nullable(),
  credits: z.array(z.object({ id: z.string(), amount: minorAmount, currency: currencyCode, status: text, type: text,
    pre_payment_amount: minorAmount, post_payment_amount: minorAmount, pdf: providerDocumentUrl,
    refunds: z.array(z.object({ id: z.string(), amount: minorAmount })).nullable() })).nullable(),
  refunds: z.array(z.object({ id: z.string(), amount: minorAmount, currency: currencyCode, status: text,
    charge_id: text, payment_intent_id: text, created_at: date })).nullable(),
});
export type BillingInvoice = z.infer<typeof invoiceSchema>;
export const invoiceListSchema = z.union([z.array(invoiceSchema), z.object({ invoices: z.array(invoiceSchema), count: z.number().int().nonnegative().optional() })])
  .transform((value) => Array.isArray(value) ? value : value.invoices);

export const usageSchema = z.object({ usage_type: z.string(), total_used: z.number().finite().nonnegative(),
  allocated: z.number().finite().nonnegative(), remaining: z.number().finite().nonnegative(), overage: z.number().finite().nonnegative(),
  unlimited: z.boolean(), metering_period: z.literal("calendar_month"),
});
export type BillingUsage = z.infer<typeof usageSchema>;
export const dailyUsageSchema = z.array(z.object({ date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  minutesUsed: z.number().finite().nonnegative(), secondsUsed: z.number().int().safe().nonnegative(), totalCalls: z.number().int().nonnegative(),
  successfulCalls: z.number().int().nonnegative(), failedCalls: z.number().int().nonnegative(),
}));
export const overageAlertsSchema = z.array(z.object({ type: z.enum(["minutes", "concurrency"]),
  currentUsage: z.number().finite(), limit: z.number().finite(), exceededBy: z.number().finite(),
  estimatedCharge: minorAmount, currency: currencyCode, currency_exponent: currencyExponent.default(null),
  severity: z.enum(["warning", "critical"]),
}));

export const billingLedgerSchema = z.object({
  id: z.string(), order_id: z.string().uuid().nullable(), provider_event_id: text, provider_payment_id: text,
  kind: z.enum(["topup", "refund", "adjustment", "dispute"]), minutes_delta: z.number().int().safe(),
  amount_cents: minorAmount, currency: currencyCode, currency_exponent: currencyExponent,
  note: text, created_at: date,
});
export type BillingLedgerEntry = z.infer<typeof billingLedgerSchema>;
export const ledgerListSchema = z.union([z.array(billingLedgerSchema), z.object({ entries: z.array(billingLedgerSchema) })])
  .transform((value) => Array.isArray(value) ? value : value.entries);
