"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

import { z } from "zod";
import { currencyCode, currencyExponent, formatMinorMoney, ledgerListSchema, type BillingLedgerEntry } from "@/lib/billing-read";

const recordedDate = z.string().datetime({ offset: true }).nullable();
export const topupPackageSchema = z.object({ code: z.string(), name: z.string(), minutes: z.number().int().positive(),
  price_cents: z.number().int().safe().nonnegative(), currency: currencyCode, currency_exponent: currencyExponent,
  expires_days: z.number().int().positive().nullable(), price_per_minute_cents: z.number().finite().nonnegative(),
});
export type TopupPackage = z.infer<typeof topupPackageSchema>;
export const topupBalanceSchema = z.object({ allocated: z.number().finite(), used_minutes: z.number().finite().nonnegative(),
  remaining_minutes: z.number().finite(), unlimited: z.boolean(), exhausted: z.boolean(), purchased_minutes: z.number().finite(),
});
export type TopupBalance = z.infer<typeof topupBalanceSchema>;
export const topupOrderSchema = z.object({ id: z.string().uuid(), package_code: z.string(), minutes: z.number().int().safe(),
  price_cents: z.number().int().safe(), currency: currencyCode, currency_exponent: currencyExponent,
  status: z.enum(["pending", "paid", "failed", "cancelled", "refunded", "disputed"]),
  created_at: recordedDate, paid_at: recordedDate, provider_payment_id: z.string().nullable(),
  refund_details: z.object({ detail_status: z.enum(["complete", "partial", "unavailable"]), captured_at: recordedDate,
    source_event_id: z.string().nullable(), refunds: z.array(z.object({ id: z.string(),
      status: z.enum(["pending", "requires_action", "succeeded", "failed", "canceled"]).nullable(),
      amount: z.number().int().safe().nullable(), currency: currencyCode, currency_exponent: currencyExponent, created_at: recordedDate,
    })).nullable(),
  }),
});
export type TopupOrder = z.infer<typeof topupOrderSchema>;
export type LedgerEntry = BillingLedgerEntry;

export const topupKeys = {
  packages: () => ["billing", "topups", "packages"] as const,
  balance: () => ["billing", "topups", "balance"] as const,
  orders: () => ["billing", "topups", "orders"] as const,
  ledger: () => ["billing", "topups", "ledger"] as const,
};

export function useTopupPackages() {
  return useQuery({
    queryKey: topupKeys.packages(),
    queryFn: async () => z.array(topupPackageSchema).parse(await api.request({ path: "/billing/topups/packages" })),
    // The catalogue changes about as often as the pricing page does.
    staleTime: 5 * 60 * 1000,
  });
}

export function useTopupBalance(pollMs?: number) {
  return useQuery({
    queryKey: topupKeys.balance(),
    queryFn: async () => topupBalanceSchema.parse(await api.request({ path: "/billing/topups/balance" })),
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
      return z.object({ orders: z.array(topupOrderSchema) }).parse(r).orders;
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
      return ledgerListSchema.parse(r);
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
      // Refresh the saved order. A pending row remains unconfirmed and does
      // not establish whether the provider collected a payment.
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

/** Missing currency or precision is unavailable, never an assumed GBP price. */
export function formatMoney(minor: number, currency: string | null, exponent: number | null) {
  return formatMinorMoney(minor, currency, exponent);
}

export const ORDER_STATUS_LABEL: Record<TopupOrder["status"], string> = {
  pending: "Payment unconfirmed",
  paid: "Paid order",
  failed: "Payment failed",
  cancelled: "Cancelled",
  refunded: "Refund accounting recorded",
  disputed: "Dispute accounting recorded",
};

export const ORDER_STATUS_TONE: Record<TopupOrder["status"], string> = {
  pending: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
  paid: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  failed: "bg-red-500/10 text-red-600 dark:text-red-400",
  cancelled: "bg-muted text-muted-foreground",
  refunded: "bg-blue-500/10 text-blue-600 dark:text-blue-400",
  disputed: "bg-red-500/10 text-red-600 dark:text-red-400",
};
