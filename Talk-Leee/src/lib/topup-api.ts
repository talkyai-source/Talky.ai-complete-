"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

/**
 * Minute top-ups (goals.md §9).
 *
 * WHY THIS DOES NOT USE `billingFetch`
 * ------------------------------------
 * The shared billing helper returns `null` on any error so read-only pages can
 * render an honest empty state. That is the right trade for a usage chart and
 * the wrong one for a purchase: a failed checkout would come back as `null` and
 * be indistinguishable from a successful one that returned nothing. Purchases
 * go through `api.request` directly so failures throw and the button can say
 * what went wrong.
 *
 * WHY RETURNING FROM STRIPE IS NOT PROOF OF ANYTHING
 * ---------------------------------------------------
 * The browser comes back from the payment page as soon as the card is
 * accepted. The minutes are credited by a webhook that arrives separately and
 * may be a second or two behind. So the success state polls the balance until
 * it moves rather than announcing a number it has not seen.
 */

export type TopupPackage = {
  code: string;
  name: string;
  minutes: number;
  price_cents: number;
  currency: string;
  expires_days: number | null;
  price_per_minute_cents: number;
};

export type TopupBalance = {
  allocated: number;
  used_minutes: number;
  remaining_minutes: number;
  unlimited: boolean;
  exhausted: boolean;
  purchased_minutes: number;
};

export type TopupOrder = {
  id: string;
  package_code: string;
  minutes: number;
  price_cents: number;
  currency: string;
  status:
    | "pending"
    | "paid"
    | "failed"
    | "cancelled"
    | "refunded"
    | "disputed";
  created_at: string | null;
  paid_at: string | null;
};

export type LedgerEntry = {
  kind: "topup" | "refund" | "adjustment" | "dispute";
  minutes_delta: number;
  amount_cents: number;
  currency: string | null;
  note: string | null;
  created_at: string | null;
};

export const topupKeys = {
  packages: () => ["billing", "topups", "packages"] as const,
  balance: () => ["billing", "topups", "balance"] as const,
  orders: () => ["billing", "topups", "orders"] as const,
  ledger: () => ["billing", "topups", "ledger"] as const,
};

export function useTopupPackages() {
  return useQuery({
    queryKey: topupKeys.packages(),
    queryFn: () => api.request<TopupPackage[]>({ path: "/billing/topups/packages" }),
    // The catalogue changes about as often as the pricing page does.
    staleTime: 5 * 60 * 1000,
  });
}

export function useTopupBalance(pollMs?: number) {
  return useQuery({
    queryKey: topupKeys.balance(),
    queryFn: () => api.request<TopupBalance>({ path: "/billing/topups/balance" }),
    refetchInterval: pollMs,
  });
}

export function useTopupOrders() {
  return useQuery({
    queryKey: topupKeys.orders(),
    queryFn: async () => {
      const r = await api.request<{ orders: TopupOrder[] }>({
        path: "/billing/topups/orders",
      });
      return r?.orders ?? [];
    },
  });
}

export function useTopupLedger() {
  return useQuery({
    queryKey: topupKeys.ledger(),
    queryFn: async () => {
      const r = await api.request<{ entries: LedgerEntry[] }>({
        path: "/billing/topups/ledger",
      });
      return r?.entries ?? [];
    },
  });
}

export type CheckoutResult = {
  order_id: string;
  session_id: string;
  checkout_url: string;
  minutes: number;
  price_cents: number;
  currency: string;
  mock_mode: boolean;
  message?: string | null;
};

export function useStartTopup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (packageCode: string) =>
      api.request<CheckoutResult>({
        path: "/billing/topups/checkout",
        method: "POST",
        body: { package_code: packageCode },
      }),
    onSuccess: () => {
      // The order exists now even though it is unpaid, and the history list
      // shows pending rows — so a customer who abandons the payment page can
      // see that nothing was charged rather than wondering.
      qc.invalidateQueries({ queryKey: topupKeys.orders() });
    },
  });
}

// ── the decisions the card makes, as functions rather than inline JSX ──
//
// These are pulled out because each one is a rule with a wrong answer that
// costs something real, and a rule buried in a ternary inside a render tree is
// a rule nobody can test.

/**
 * An unlimited plan must never be offered a top-up.
 *
 * `minutes_allocated <= 0` is the unlimited sentinel across this system. The
 * backend refuses to add minutes to such a tenant, so showing them a Buy
 * button offers a purchase that would take their money and change nothing.
 */
export function canTopUp(balance: TopupBalance | null): boolean {
  if (!balance) return false;
  return !balance.unlimited;
}

/** Amber when under 15% remains — enough warning to buy before calls stop. */
export function isLowBalance(balance: TopupBalance | null): boolean {
  if (!balance || balance.unlimited) return false;
  if (balance.allocated <= 0) return false;
  return balance.remaining_minutes / balance.allocated < 0.15;
}

/**
 * Have the minutes actually landed?
 *
 * Returning from the payment page proves the card was accepted, not that the
 * webhook has been processed. The only honest signal is the ledger total
 * moving, so this compares against the value read on arrival.
 *
 * Deliberately `>` and not `!==`: a refund processed in the same window moves
 * the total DOWN, and reporting that as "your minutes are ready" would be a lie
 * in the one direction that matters.
 */
export function creditHasLanded(
  baselinePurchased: number | null,
  current: TopupBalance | null,
): boolean {
  if (baselinePurchased === null || !current) return false;
  return current.purchased_minutes > baselinePurchased;
}

/** Money, formatted from the minor units the API actually returns. */
export function formatMoney(cents: number, currency = "GBP") {
  return new Intl.NumberFormat("en-GB", {
    style: "currency",
    currency: currency || "GBP",
  }).format(cents / 100);
}

export const ORDER_STATUS_LABEL: Record<TopupOrder["status"], string> = {
  pending: "Awaiting payment",
  paid: "Added",
  failed: "Payment failed",
  cancelled: "Cancelled",
  refunded: "Refunded",
  disputed: "Disputed",
};

export const ORDER_STATUS_TONE: Record<TopupOrder["status"], string> = {
  pending: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
  paid: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  failed: "bg-red-500/10 text-red-600 dark:text-red-400",
  cancelled: "bg-muted text-muted-foreground",
  refunded: "bg-blue-500/10 text-blue-600 dark:text-blue-400",
  disputed: "bg-red-500/10 text-red-600 dark:text-red-400",
};
