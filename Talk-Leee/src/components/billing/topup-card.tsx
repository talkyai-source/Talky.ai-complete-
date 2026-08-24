"use client";

import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Loader2,
  Plus,
  Zap,
} from "lucide-react";
import {
  canTopUp,
  creditHasLanded,
  formatMoney,
  isLowBalance,
  ORDER_STATUS_LABEL,
  ORDER_STATUS_TONE,
  topupKeys,
  useStartTopup,
  useTopupBalance,
  useTopupOrders,
  useTopupPackages,
  type TopupPackage,
} from "@/lib/topup-api";

/**
 * Buy call minutes outside the plan cycle (goals.md §9).
 *
 * THREE THINGS THIS GETS RIGHT THAT ARE EASY TO GET WRONG
 * --------------------------------------------------------
 * 1. A double click must not buy twice. The button locks on the first click
 *    and stays locked through the redirect, because a second order is a second
 *    real charge — not a duplicate row we can clean up later.
 *
 * 2. Coming back from Stripe is not proof the minutes landed. The credit is
 *    applied by a webhook that arrives on its own schedule. So the success
 *    state polls the balance and only confirms once the number has actually
 *    moved; until then it says it is confirming, which is the truth.
 *
 * 3. An unlimited plan is not offered a top-up at all. `minutes_allocated <= 0`
 *    means uncapped throughout this system, and adding 250 to it would replace
 *    "no limit" with "250 minutes" — a purchase that leaves the customer worse
 *    off than before they paid.
 */

const POLL_MS = 2500;
const POLL_GIVE_UP_MS = 45_000;

export function TopupCard() {
  const router = useRouter();
  const params = useSearchParams();
  const qc = useQueryClient();

  const returned = params.get("topup"); // "success" | "cancelled" | null
  const [confirming, setConfirming] = useState(returned === "success");

  const packagesQ = useTopupPackages();
  const balanceQ = useTopupBalance(confirming ? POLL_MS : undefined);
  const ordersQ = useTopupOrders();
  const start = useStartTopup();

  const balance = balanceQ.data ?? null;
  const packages = packagesQ.data ?? [];
  const orders = ordersQ.data ?? [];

  // The balance we came back with. When it moves, the webhook has landed.
  const baseline = useRef<number | null>(null);
  const startedAt = useRef<number>(Date.now());
  const [credited, setCredited] = useState(false);

  useEffect(() => {
    if (!confirming || balance == null) return;
    if (baseline.current === null) {
      baseline.current = balance.purchased_minutes;
      startedAt.current = Date.now();
      return;
    }
    if (creditHasLanded(baseline.current, balance)) {
      setConfirming(false);
      setCredited(true);
      qc.invalidateQueries({ queryKey: topupKeys.orders() });
      qc.invalidateQueries({ queryKey: ["billing"] });
      return;
    }
    if (Date.now() - startedAt.current > POLL_GIVE_UP_MS) {
      // Stop spinning forever. The payment may still be settling; saying so is
      // better than a spinner that never resolves or a success we can't see.
      setConfirming(false);
    }
  }, [confirming, balance, qc]);

  // Clear the query string once handled so a refresh does not replay the
  // confirmation banner.
  useEffect(() => {
    if (!returned) return;
    if (confirming) return;
    const t = setTimeout(() => router.replace("/billing"), 6000);
    return () => clearTimeout(t);
  }, [returned, confirming, router]);

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
              "purchase cannot be completed. Nothing has been charged.",
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

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2">
              <Zap className="h-5 w-5" aria-hidden /> Top up minutes
            </CardTitle>
            <CardDescription>
              Add call minutes straight away, without changing your plan.
            </CardDescription>
          </div>
          {balance && !unlimited ? (
            <div className="text-right">
              <div className="text-xs font-semibold text-muted-foreground">
                Minutes remaining
              </div>
              <div
                className={`text-2xl font-bold tabular-nums ${
                  lowBalance ? "text-amber-600 dark:text-amber-400" : "text-foreground"
                }`}
              >
                {balance.remaining_minutes.toLocaleString()}
              </div>
              <div className="text-xs text-muted-foreground tabular-nums">
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

      <CardContent className="space-y-4">
        {/* ── returning from the payment page ── */}
        {confirming ? (
          <div
            role="status"
            className="flex items-center gap-3 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm"
          >
            <Loader2 className="h-4 w-4 shrink-0 animate-spin" aria-hidden />
            <span>
              Payment received — confirming with your bank and adding the minutes.
              This usually takes a few seconds.
            </span>
          </div>
        ) : null}

        {credited ? (
          <div
            role="status"
            className="flex items-center gap-3 rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-3 text-sm"
          >
            <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden />
            <span>Your minutes have been added and are ready to use.</span>
          </div>
        ) : null}

        {returned === "success" && !confirming && !credited ? (
          <div
            role="status"
            className="flex items-start gap-3 rounded-lg border border-border bg-muted/40 p-3 text-sm"
          >
            <Clock className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
            <span>
              Your payment went through. The minutes are still being confirmed —
              they will appear here shortly, and the order below will move to
              &ldquo;Added&rdquo;. Nothing further is needed from you.
            </span>
          </div>
        ) : null}

        {returned === "cancelled" ? (
          <div
            role="status"
            className="flex items-center gap-3 rounded-lg border border-border bg-muted/40 p-3 text-sm text-muted-foreground"
          >
            <span>Checkout cancelled. You have not been charged.</span>
          </div>
        ) : null}

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
              We could not start the purchase, so nothing has been charged.
              Please try again in a moment.
            </span>
          </div>
        ) : null}

        {/* ── the catalogue ── */}
        {unlimited ? (
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
            support and we will sort it out.
          </p>
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {packages.map((pkg) => {
              const busy = pending && chosen === pkg.code;
              return (
                <div
                  key={pkg.code}
                  className="flex flex-col rounded-lg border border-border p-4 transition-colors hover:border-primary/50"
                >
                  <div className="text-2xl font-bold tabular-nums text-foreground">
                    {pkg.minutes.toLocaleString()}
                  </div>
                  <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    minutes
                  </div>
                  <div className="mt-3 text-lg font-semibold text-foreground">
                    {formatMoney(pkg.price_cents, pkg.currency)}
                  </div>
                  <div className="text-xs text-muted-foreground tabular-nums">
                    {formatMoney(pkg.price_per_minute_cents, pkg.currency)} per minute
                  </div>
                  {pkg.expires_days ? (
                    <div className="mt-1 text-xs text-muted-foreground">
                      Valid for {pkg.expires_days} days
                    </div>
                  ) : (
                    <div className="mt-1 text-xs text-muted-foreground">
                      Never expires
                    </div>
                  )}
                  <Button
                    className="mt-4 w-full"
                    onClick={() => buy(pkg)}
                    disabled={pending}
                    aria-label={`Buy ${pkg.minutes} minutes for ${formatMoney(pkg.price_cents, pkg.currency)}`}
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
        {orders.length > 0 ? (
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
                      {formatMoney(o.price_cents, o.currency)}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {o.paid_at
                        ? new Date(o.paid_at).toLocaleString()
                        : o.created_at
                          ? new Date(o.created_at).toLocaleString()
                          : ""}
                    </div>
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
        ) : null}
      </CardContent>
    </Card>
  );
}
