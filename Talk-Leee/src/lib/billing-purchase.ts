import { z } from "zod";
import { api } from "@/lib/api";
import { env } from "@/lib/env";

const uuid = z.string().uuid().transform(value => value.toLowerCase());
const priceFields = {
  id: uuid, kind: z.enum(["free", "stripe"]),
  interval: z.enum(["month", "year"]), interval_count: z.literal(1),
  amount_minor: z.number().int().nonnegative().safe(),
  currency: z.string().regex(/^[a-z]{3}$/), currency_exponent: z.union([z.literal(0), z.literal(2), z.literal(3)]),
};
export const billingPriceSchema = z.object({
  ...priceFields, active: z.boolean(), checkout_available: z.boolean(), unavailable_reason: z.string().nullable().optional(),
});
export const purchasedPriceSchema = z.object({ ...priceFields, plan_id: z.string().min(1), plan_name: z.string().min(1) });
export const billingCatalogSchema = z.array(z.object({
  id: z.string().min(1), name: z.string().min(1), description: z.string().nullable().optional(),
  minutes: z.number().nonnegative(), concurrent_calls: z.number().nonnegative(),
  features: z.array(z.string()).default([]), popular: z.boolean().optional(),
  billing_mode: z.enum(["live", "test", "mock", "disabled", "unconfigured"]), price_options: z.array(billingPriceSchema),
}));
export type BillingCatalog = z.infer<typeof billingCatalogSchema>;
export type BillingPrice = z.infer<typeof billingPriceSchema>;
export type PurchasedPrice = z.infer<typeof purchasedPriceSchema>;
export const billingSubscriptionSchema = z.object({
  status: z.string(), plan_id: z.string().nullable().optional(), plan_name: z.string().nullable().optional(),
  current_period_start: z.string().nullable().optional(), current_period_end: z.string().nullable().optional(),
  cancel_at_period_end: z.boolean().default(false),
  minutes_state: z.enum(["known", "unlimited", "unavailable"]).default("unavailable"),
  minutes_allocated: z.number(), minutes_used: z.number(), minutes_remaining: z.number(),
  purchased_price_option: purchasedPriceSchema.nullable().default(null), billing_portal_available: z.boolean().default(false),
});
export type BillingSubscription = z.infer<typeof billingSubscriptionSchema>;
export const checkoutAttemptSchema = z.object({
  request_id: uuid, state: z.enum(["open", "activated", "pending", "expired", "failed"]),
  session_id: z.string().nullable(), checkout_url: z.string().url().nullable(),
  price_option: purchasedPriceSchema, mock_mode: z.literal(false), message: z.string().nullable().optional(),
});
export type CheckoutAttempt = z.infer<typeof checkoutAttemptSchema>;
export const savedPurchaseSchema = z.object({ request_id: uuid, price_option: purchasedPriceSchema });
export type SavedPurchase = z.infer<typeof savedPurchaseSchema>;

export function formatPurchasedPrice(price: PurchasedPrice | BillingPrice): string {
  if (price.kind === "free") return "Free";
  return `${new Intl.NumberFormat(undefined, {
    style: "currency", currency: price.currency.toUpperCase(),
    minimumFractionDigits: price.currency_exponent, maximumFractionDigits: price.currency_exponent,
  }).format(price.amount_minor / (10 ** price.currency_exponent))} / ${price.interval}`;
}
export function hasManagedSubscription(subscription: BillingSubscription | null): boolean {
  if (!subscription || ["inactive", "canceled", "cancelled", "incomplete_expired"].includes(subscription.status)) return false;
  if (subscription.purchased_price_option?.kind === "free") return false;
  return subscription.purchased_price_option?.kind === "stripe" || subscription.billing_portal_available;
}
export function requireBillingBackend(): void {
  if (!env.NEXT_PUBLIC_API_BASE_URL) throw new Error("Billing is unavailable because its service connection is not configured. Please contact support; no purchase was started.");
}
export function approvedCheckoutUrl(url: string, kind: "checkout" | "portal" = "checkout"): string {
  const parsed = new URL(url);
  const expectedHost = kind === "portal" ? "billing.stripe.com" : "checkout.stripe.com";
  if (parsed.protocol !== "https:" || parsed.host !== expectedHost || parsed.username || parsed.password) throw new Error("The payment destination could not be verified. Your saved request is unchanged.");
  return parsed.href;
}
function validateAttempt(data: unknown, saved: SavedPurchase): CheckoutAttempt {
  const parsed = checkoutAttemptSchema.safeParse(data);
  if (!parsed.success) throw new Error("The billing response could not be verified. Keep the saved request and check again.");
  const result = parsed.data;
  const keys = ["id", "plan_id", "kind", "interval", "interval_count", "amount_minor", "currency", "currency_exponent"] as const;
  if (result.request_id !== saved.request_id || keys.some(key => result.price_option[key] !== saved.price_option[key])) {
    throw new Error("The returned purchase details do not match your saved selection. Keep this request reference and contact support before starting another purchase.");
  }
  if (result.state === "open" && (!result.checkout_url || !result.session_id)) throw new Error("Checkout is not ready. Check the saved request again.");
  if (result.checkout_url) approvedCheckoutUrl(result.checkout_url);
  return result;
}
export function validateOutstandingAttempt(data: unknown): CheckoutAttempt {
  const parsed = checkoutAttemptSchema.safeParse(data);
  if (!parsed.success) throw new Error("The existing purchase could not be verified. Keep your request reference and contact support.");
  return validateAttempt(parsed.data, { request_id: parsed.data.request_id, price_option: parsed.data.price_option });
}
export async function startBillingCheckout(saved: SavedPurchase, signal?: AbortSignal): Promise<CheckoutAttempt> {
  requireBillingBackend();
  return validateAttempt(await api.request({
    path: "/billing/create-checkout-session", method: "POST", timeoutMs: 15_000, signal,
    body: { request_id: saved.request_id, price_option_id: saved.price_option.id },
  }), saved);
}
export async function readBillingCheckout(saved: SavedPurchase, signal?: AbortSignal): Promise<CheckoutAttempt> {
  requireBillingBackend();
  return validateAttempt(await api.request({ path: `/billing/checkout-attempts/${encodeURIComponent(saved.request_id)}`, method: "GET", timeoutMs: 12_000, signal }), saved);
}
export async function openBillingPortal(signal?: AbortSignal): Promise<string> {
  requireBillingBackend();
  const result = z.object({ portal_url: z.string().url(), mock_mode: z.literal(false) }).safeParse(await api.request({ path: "/billing/portal", method: "POST", body: {}, timeoutMs: 12_000, signal }));
  if (!result.success) throw new Error("The billing portal response could not be verified. Please try again.");
  return approvedCheckoutUrl(result.data.portal_url, "portal");
}
const storageKey = (scope: string) => `talklee.billing-purchase.v1:${encodeURIComponent(scope)}`;
export function readSavedPurchase(scope: string): { saved: SavedPurchase | null; available: boolean } {
  try {
    const raw = window.sessionStorage.getItem(storageKey(scope));
    if (!raw) return { saved: null, available: true };
    const result = raw.length < 4000 ? savedPurchaseSchema.safeParse(JSON.parse(raw)) : null;
    if (!result?.success) return { saved: null, available: false };
    return { saved: result.data, available: true };
  } catch { return { saved: null, available: false }; }
}
export function storeSavedPurchase(scope: string, saved: SavedPurchase | null): boolean {
  try {
    if (saved) window.sessionStorage.setItem(storageKey(scope), JSON.stringify(saved));
    else window.sessionStorage.removeItem(storageKey(scope));
    return true;
  } catch { return false; }
}
