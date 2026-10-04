"use client";

import { useState } from "react";
import { useSearchParams } from "next/navigation";
import { BillingSupport, ProviderRefundFacts } from "@/components/billing/invoice-view";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  AlertTriangle,
  Loader2,
  Plus,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Zap,
} from "lucide-react";
import { isApiClientError } from "@/lib/http-client";
import {
  canTopUp,
  formatMoney,
  isLowBalance,
  ORDER_STATUS_LABEL,
  ORDER_STATUS_TONE,
  useStartTopup,
  useTopupBalance,
  useTopupOrders,
  useTopupPackages,
  type TopupPackage,
} from "@/lib/topup-api";

/** The backend's own words where it gave any, never a cause we made up. */
function formatTopupError(err: unknown): string {
  if (isApiClientError(err)) {
    if (err.status === 403) return "You do not have permission to view billing for this account.";
    if (err.status === 401) return "Your session has expired.";
    return err.message;
  }
  return err instanceof Error ? err.message : "The request did not complete.";
}

export function TopupCard() {
  const params = useSearchParams();

  const returned = params.get("topup"); // "success" | "cancelled" | null
  const returnedOrder = params.get("order_id");

  const packagesQ = useTopupPackages();
  const balanceQ = useTopupBalance();
  const ordersQ = useTopupOrders();
  const start = useStartTopup();

  const balance = balanceQ.data ?? null;
  const packages = packagesQ.data ?? [];
  const orders = ordersQ.data ?? [];

  // A failed balance or catalogue fetch is not an empty catalogue. Before this
  // gate, a 403 on the balance left `balance` null and the card spun forever on
  // "Loading bundles…", and a failed catalogue said "No top-up bundles are
  // available on your account" — both of which describe an account state that
  // nothing had checked.
  const catalogueFailed = balanceQ.isError || packagesQ.isError;
  const catalogueError = balanceQ.isError ? balanceQ.error : packagesQ.error;

  const pending = start.isPending;
  const [chosen, setChosen] = useState<string | null>(null);
  const [mockNotice, setMockNotice] = useState<string | null>(null);

  async function buy(pkg: TopupPackage) {
    if (pending) return; // the double-click guard — a second order is a second charge
    setMockNotice(null);
    setChosen(pkg.code);
    try {
      const result = await start.mutateAsync(pkg.code);

      // Mock mode means no payment provider is configured on this
      // environment. Following the fake checkout URL would land the customer
      // on a success page for a payment that never happened and then spin
      // forever waiting for a webhook that will never arrive. Say so instead.
      if (result?.mock_mode) {
        setMockNotice(
          result.message ??
            "Card payments are not configured on this environment yet, so this " +
              "purchase cannot be completed. This read cannot confirm payment status.",
        );
        setChosen(null);
        return;
      }

      if (result?.checkout_url) {
        // Leaves the app entirely. The button stays disabled behind us because
        // `pending` never clears before navigation.
        window.location.assign(result.checkout_url);
      }
    } catch {
      setChosen(null);
    }
  }

  // `balance` is null while the first fetch is in flight, so the catalogue is
  // hidden until we know — showing Buy buttons to an unlimited tenant for a
  // second and then pulling them away is worse than a beat of nothing.
  const offerTopups = canTopUp(balance);
  const unlimited = balance?.unlimited ?? false;
  const lowBalance = isLowBalance(balance);
  const bestValueCode = packages.length > 1 && packages.every((pkg) => pkg.currency === packages[0].currency && pkg.currency_exponent === packages[0].currency_exponent)
    ? packages.reduce((best, pkg) =>
        pkg.price_per_minute_cents < best.price_per_minute_cents ? pkg : best,
      ).code
    : null;

  return (
    <Card>
      <CardHeader className="space-y-1 p-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <CardTitle className="flex items-center gap-1.5 text-base">
              <Zap className="h-4 w-4" aria-hidden /> Top up minutes
            </CardTitle>
            <CardDescription className="text-xs">
              Buy call minutes without changing your plan. Credit follows verified payment.
            </CardDescription>
          </div>
          {balance && !unlimited ? (
            <div className="text-right">
              <div className="flex items-baseline justify-end gap-1.5 whitespace-nowrap">
                <span className="text-[11px] font-semibold text-muted-foreground">
                  Minutes remaining
                </span>
                <span
                  className={`text-base font-bold tabular-nums ${
                    lowBalance ? "text-amber-600 dark:text-amber-400" : "text-foreground"
                  }`}
                >
                  {balance.remaining_minutes.toLocaleString()}
                </span>
              </div>
              <div className="whitespace-nowrap text-[11px] text-muted-foreground tabular-nums">
                {balance.used_minutes.toLocaleString()} of{" "}
                {balance.allocated.toLocaleString()} used
                {balance.purchased_minutes > 0 ? (
                  <>
                    {" · "}
                    {balance.purchased_minutes.toLocaleString()} topped up
                  </>
                ) : null}
              </div>
            </div>
          ) : null}
        </div>
      </CardHeader>

      <CardContent className="space-y-3 p-4 pt-0">
        {/* ── returning from the payment page ── */}
        {returned && <div role="status" className="space-y-2 rounded-lg border p-3 text-sm">
          <p>Returned from checkout. This browser return does not confirm a payment, cancellation or minute credit. Check the saved order status below.</p>
          {returnedOrder && orders.some((order) => order.id === returnedOrder) && <p className="break-all">Order reference: {returnedOrder}</p>}
          <Button variant="outline" size="sm" onClick={() => { void ordersQ.refetch(); void balanceQ.refetch(); }}>Refresh saved status</Button>
        </div>}

        {mockNotice ? (
          <div
            role="alert"
            className="flex items-start gap-3 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm"
          >
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden />
            <span>{mockNotice}</span>
          </div>
        ) : null}

        {start.isError ? (
          <div
            role="alert"
            className="flex items-start gap-3 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm"
          >
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-red-600 dark:text-red-400" aria-hidden />
            <span>
              The purchase result was not confirmed. Review saved orders before trying again.
              Contact the billing team if the saved status remains unclear.
            </span>
          </div>
        ) : null}

        {/* ── the catalogue ── */}
        {catalogueFailed ? (
          <div
            role="alert"
            className="flex flex-col gap-3 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm sm:flex-row sm:items-center sm:justify-between"
          >
            <div className="flex items-start gap-3">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-red-600 dark:text-red-400" aria-hidden />
              <div className="text-red-800 dark:text-red-300">
                <div className="font-semibold">Your minute balance did not load.</div>
                <div className="text-xs opacity-90">
                  {formatTopupError(catalogueError)} This read cannot confirm payment status, and no
                  bundles are shown until we can read your balance.
                </div>
              </div>
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="self-start sm:self-auto"
              onClick={() => {
                void balanceQ.refetch();
                void packagesQ.refetch();
              }}
            >
              <RotateCcw className="mr-1 h-4 w-4" aria-hidden /> Retry
            </Button>
          </div>
        ) : unlimited ? (
          <p className="rounded-lg border border-border bg-muted/40 p-3 text-sm text-muted-foreground">
            Your plan has unlimited minutes, so there is nothing to top up.
          </p>
        ) : !offerTopups || packagesQ.isLoading ? (
          <div className="flex items-center justify-center py-8 text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> Loading bundles…
          </div>
        ) : packages.length === 0 ? (
          <p className="rounded-lg border border-border bg-muted/40 p-3 text-sm text-muted-foreground">
            No top-up bundles are available on your account right now. Contact
            the billing team for a review.
          </p>
        ) : (
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            {packages.map((pkg) => {
              const busy = pending && chosen === pkg.code;
              const bestValue = pkg.code === bestValueCode;
              return (
                <div
                  key={pkg.code}
                  className={`relative flex flex-col rounded-lg border p-3 transition-colors ${
                    bestValue ? "border-primary/50 bg-primary/[0.04]" : "border-border hover:border-primary/50"
                  }`}
                >
                  {bestValue ? (
                    <div className="absolute right-2 top-2 inline-flex items-center gap-1 rounded-full bg-primary px-2 py-0.5 text-[9px] font-bold uppercase tracking-wide text-primary-foreground">
                      <Sparkles className="h-2.5 w-2.5" aria-hidden /> Best value
                    </div>
                  ) : null}
                  <div className="text-lg font-bold tabular-nums text-foreground">
                    {pkg.minutes.toLocaleString()}
                  </div>
                  <div className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                    minutes
                  </div>
                  <div className="mt-1.5 text-sm font-semibold text-foreground">
                    {formatMoney(pkg.price_cents, pkg.currency, pkg.currency_exponent)}
                  </div>
                  {pkg.expires_days ? (
                    <div className="flex items-center gap-1 text-[11px] text-muted-foreground">
                      <ShieldCheck className="h-3 w-3" aria-hidden />
                      Valid for {pkg.expires_days} days
                    </div>
                  ) : (
                    <div className="flex items-center gap-1 text-[11px] text-muted-foreground">
                      <ShieldCheck className="h-3 w-3" aria-hidden />
                      Never expires
                    </div>
                  )}
                  <Button
                    size="sm"
                    className="mt-2 w-full"
                    onClick={() => buy(pkg)}
                    disabled={pending || pkg.currency === null || pkg.currency_exponent === null}
                    aria-label={`Buy ${pkg.minutes} minutes for ${formatMoney(pkg.price_cents, pkg.currency, pkg.currency_exponent)}`}
                  >
                    {busy ? (
                      <>
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
                        Opening checkout…
                      </>
                    ) : (
                      <>
                        <Plus className="mr-2 h-4 w-4" aria-hidden /> Buy
                      </>
                    )}
                  </Button>
                </div>
              );
            })}
          </div>
        )}

        {/* ── what has been bought ── */}
        {ordersQ.isError ? (
          <div
            role="alert"
            className="flex items-start gap-3 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm"
          >
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-red-600 dark:text-red-400" aria-hidden />
            <span className="text-red-800 dark:text-red-300">
              Your recent top-ups did not load, so this list is not shown. Any
              purchase you have made is unaffected.{" "}
              <button
                type="button"
                className="font-semibold underline underline-offset-2"
                onClick={() => void ordersQ.refetch()}
              >
                Retry
              </button>
            </span>
          </div>
        ) : ordersQ.isLoading ? <p role="status">Loading saved top-up orders…</p> : orders.length > 0 ? (
          <div className="pt-2">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Recent top-ups
            </div>
            <ul className="divide-y divide-border rounded-lg border border-border">
              {orders.slice(0, 6).map((o) => (
                <li key={o.id} className="flex items-center justify-between gap-3 p-3 text-sm">
                  <div className="min-w-0">
                    <div className="font-medium text-foreground tabular-nums">
                      {o.minutes.toLocaleString()} minutes ·{" "}
                      {formatMoney(o.price_cents, o.currency, o.currency_exponent)}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {o.paid_at
                        ? new Date(o.paid_at).toLocaleString()
                        : o.created_at
                          ? new Date(o.created_at).toLocaleString()
                          : ""}
                    </div>
                    <p className="mt-1 break-all text-xs text-muted-foreground">Order: {o.id}{o.provider_payment_id ? ` · Payment: ${o.provider_payment_id}` : ""}</p>
                    {(o.status === "refunded" || o.status === "disputed" || o.refund_details.refunds?.length || o.refund_details.captured_at) ? <div className="mt-3"><ProviderRefundFacts refunds={o.refund_details.refunds} detailStatus={o.refund_details.detail_status} capturedAt={o.refund_details.captured_at} /></div> : null}
                  </div>
                  <span
                    className={`shrink-0 rounded-full px-2.5 py-0.5 text-xs font-semibold ${ORDER_STATUS_TONE[o.status] ?? "bg-muted text-muted-foreground"}`}
                  >
                    {ORDER_STATUS_LABEL[o.status] ?? o.status}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : <p className="text-sm text-muted-foreground">No saved top-up orders recorded.</p>}
        <BillingSupport reference={orders.find((order) => order.id === returnedOrder)?.id} />
      </CardContent>
    </Card>
  );
}
