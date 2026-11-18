import { z } from "zod";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import {
  requireBillingBackend, validateAttempt, validateOutstandingAttempt,
  type CheckoutAttempt, type SavedPurchase,
} from "@/lib/billing-purchase";

const uuid = z.guid().transform(value => value.toLowerCase());

// GET /billing/config: older servers do not send these two; missing or malformed means PayPal plans are closed.
export const paypalPlansConfigSchema = z.object({
  paypal_plans_open: z.boolean().catch(false).default(false),
  paypal_plans_unavailable_reason: z.string().nullable().catch(null).default(null),
});
export type PaypalPlansConfig = z.infer<typeof paypalPlansConfigSchema>;

/** Whether PayPal plan checkout is open. A read that fails answers null ("not known" = closed, no error shown). */
export function usePaypalPlansConfig() {
  return useQuery({
    queryKey: ["billing", "paypal-plans-config"],
    queryFn: async (): Promise<PaypalPlansConfig | null> => {
      try { return paypalPlansConfigSchema.parse(await api.request({ path: "/billing/config", timeoutMs: 8_000 })); }
      catch { return null; }
    },
    staleTime: 5 * 60 * 1000,
  });
}

export async function startPaypalPlanCheckout(saved: SavedPurchase, signal?: AbortSignal): Promise<CheckoutAttempt> {
  requireBillingBackend();
  return validateAttempt(await api.request({
    path: "/billing/paypal/subscriptions/checkout", method: "POST", timeoutMs: 15_000, signal,
    body: { request_id: saved.request_id, price_option_id: saved.price_option.id },
  }), saved, "paypal");
}

export async function readPaypalPlanCheckout(requestId: string, signal?: AbortSignal): Promise<CheckoutAttempt> {
  requireBillingBackend();
  const result = validateOutstandingAttempt(await api.request({
    path: `/billing/paypal/subscriptions/${encodeURIComponent(requestId)}`, method: "GET", timeoutMs: 12_000, signal,
  }), "paypal");
  if (result.request_id !== requestId) throw new Error("The returned purchase does not match the request reference in this page address. Contact support before starting another purchase.");
  return result;
}

const cancelSchema = z.object({
  status: z.string(), cancel_at_period_end: z.boolean(), mock_mode: z.literal(false), message: z.string().nullable().optional(),
});
export type PaypalCancelResult = z.infer<typeof cancelSchema>;

export async function cancelPaypalPlan(signal?: AbortSignal): Promise<PaypalCancelResult> {
  requireBillingBackend();
  const result = cancelSchema.safeParse(await api.request({ path: "/billing/cancel", method: "POST", timeoutMs: 15_000, signal }));
  if (!result.success) throw new Error("The cancellation answer could not be verified. Refresh this page to see whether your plan was cancelled.");
  return result.data;
}

/** Where PayPal sent the customer back to: `/billing?checkout=success|cancelled&provider=paypal&request_id=<uuid>`.
 * Other parameters PayPal adds (subscription_id, ba_token, token) are ignored and never sent anywhere. */
export function paypalPlanReturnFromUrl(search: string): { outcome: "success" | "cancelled"; requestId: string } | null {
  const params = new URLSearchParams(search);
  if (params.get("provider") !== "paypal") return null;
  const outcome = params.get("checkout");
  if (outcome !== "success" && outcome !== "cancelled") return null;
  const id = uuid.safeParse(params.get("request_id"));
  return id.success ? { outcome, requestId: id.data } : null;
}

export function formatPlanDate(iso: string | null | undefined): string {
  const date = iso ? new Date(iso) : null;
  return date && !Number.isNaN(date.getTime()) ? date.toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" }) : "the end of the paid period";
}
