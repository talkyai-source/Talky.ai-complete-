"use client";

import Link from "next/link";
import { useState, type ReactNode } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  AlertTriangle,
  ArrowRight,
  CalendarDays,
  CheckCircle,
  Clock,
  CreditCard,
  FileText,
  Layers3,
  Loader2,
  Phone,
  RotateCcw,
  Sparkles,
  TrendingUp,
  XCircle,
} from "lucide-react";
import { ErrorState } from "@/components/states/page-states";
import { isApiClientError } from "@/lib/http-client";
import { formatPurchasedPrice, type BillingSubscription } from "@/lib/billing-purchase";
import { formatMinorMoney, type BillingInvoice, type BillingUsage, type BillingLedgerEntry } from "@/lib/billing-read";
import {
  useBillingPlan,
  useBillingUsage,
  useDailyUsage,
  useBillingInvoices,
  useOverageAlerts,
  useBillingAdjustments,
} from "@/lib/billing-api";

/**
 * The body of /billing.
 *
 * WHY THIS IS A COMPONENT AND NOT THE PAGE FILE
 * ---------------------------------------------
 * The page wraps this in `DashboardLayout`, which needs the Next app-router
 * context and the auth session. Keeping the money-facing logic out here means
 * the three states below can be rendered and asserted in a test.
 *
 * THE THREE STATES, AND WHY THEY MUST NOT BLUR
 * --------------------------------------------
 * Every figure on this page comes from `/billing/subscription` or
 * `/billing/usage`. When one of those requests fails there is no honest number
 * to show, so the page must say the data did not load. It previously showed
 * "0 of 0 minutes used" and "No invoices yet" instead — because the fetch
 * helper turned every failure into `null`, and `null` and "you have used
 * nothing" render identically. A customer looking at that had no way to tell
 * a 403 from a clean bill.
 */

type Subscription = BillingSubscription;

type UsageSummary = BillingUsage;

type DailyUsageDay = {
  date: string;
  minutesUsed: number;
  secondsUsed: number;
  totalCalls: number;
  successfulCalls: number;
  failedCalls: number;
};

type InvoiceRow = BillingInvoice;

type OverageAlertRow = {
  type: "minutes" | "concurrency";
  currentUsage: number;
  limit: number;
  exceededBy: number;
  estimatedCharge: number | null;
  currency: string | null;
  currency_exponent: number | null;
  severity: "warning" | "critical";
};

function formatDate(iso: string | null | undefined) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function formatDateRange(start: string | null | undefined, end: string | null | undefined) {
  if (!start || !end) return "—";
  const s = new Date(start).toLocaleDateString(undefined, { month: "long", day: "numeric" });
  const e = new Date(end).toLocaleDateString(undefined, { month: "long", day: "numeric" });
  return `${s} – ${e}`;
}

/** The backend's own words where it gave any, never a cause we made up. */
export function formatBillingError(err: unknown): string {
  if (isApiClientError(err)) {
    if (err.status === 403) return "You do not have permission to view billing for this account.";
    if (err.status === 401) return "Your session has expired.";
    return err.message;
  }
  return err instanceof Error ? err.message : "The request did not complete.";
}

function statusBadge(state: string) {
  const s = (state || "").toLowerCase();
  const map: Record<string, { label: string; className: string }> = {
    active: { label: "Active", className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" },
    trialing: { label: "Trial", className: "border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-400" },
    past_due: { label: "Past Due", className: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400" },
    inactive: { label: "Inactive", className: "border-gray-500/30 bg-gray-500/10 text-gray-700 dark:text-gray-400" },
    // Both spellings are live: Stripe emits "canceled" (one L) and the backend
    // now canonicalises to "cancelled" (two Ls, matching the admin archive verb
    // and the call guard). Legacy rows still carry the one-L form, so the badge
    // must render either — an unmapped value falls through to "Unknown", which
    // would tell a cancelled customer nothing about their own subscription.
    canceled: { label: "Canceled", className: "border-gray-500/30 bg-gray-500/10 text-gray-700 dark:text-gray-400" },
    cancelled: { label: "Canceled", className: "border-gray-500/30 bg-gray-500/10 text-gray-700 dark:text-gray-400" },
    unpaid: { label: "Unpaid", className: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400" },
    incomplete_expired: { label: "Expired", className: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400" },
    unknown: { label: "Unknown", className: "border-gray-500/30 bg-gray-500/10 text-gray-700 dark:text-gray-400" },
  };
  const b = map[s] || map.unknown;
  return <span className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold ${b.className}`}>{b.label}</span>;
}

function invoiceStatusBadge(status: string) {
  const s = (status || "").toLowerCase();
  const map: Record<string, { label: string; className: string }> = {
    paid: { label: "Paid", className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" },
    open: { label: "Open", className: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400" },
    past_due: { label: "Past Due", className: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400" },
    void: { label: "Void", className: "border-gray-500/30 bg-gray-500/10 text-gray-700 dark:text-gray-400" },
    draft: { label: "Draft", className: "border-gray-500/30 bg-gray-500/10 text-gray-700 dark:text-gray-400" },
  };
  const b = map[s] || { label: status || "—", className: "border-gray-500/30 bg-gray-500/10 text-gray-700 dark:text-gray-400" };
  return <span className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold ${b.className}`}>{b.label}</span>;
}

/**
 * One section of the page did not load. It takes the place of that section's
 * numbers — it is never shown alongside a zero or an empty list, because the
 * whole point is that we do not know what belongs there.
 */
function SectionLoadError({ what, error, onRetry }: { what: string; error: unknown; onRetry: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-col gap-3 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 h-5 w-5 flex-shrink-0 text-red-600 dark:text-red-400" aria-hidden />
        <div className="text-red-800 dark:text-red-300">
          <div className="font-semibold">{what} did not load.</div>
          <div className="text-xs opacity-90">{formatBillingError(error)}</div>
        </div>
      </div>
      <Button type="button" variant="outline" size="sm" className="self-start sm:self-auto" onClick={onRetry}>
        <RotateCcw className="mr-1 h-4 w-4" aria-hidden /> Retry
      </Button>
    </div>
  );
}

export function BillingOverview({ topupSlot, scope }: { topupSlot?: ReactNode; scope?: string }) {
  const planQ = useBillingPlan(scope);
  const usageQ = useBillingUsage();
  const dailyQ = useDailyUsage();
  const invoicesQ = useBillingInvoices();
  const overageQ = useOverageAlerts();
  const adjQ = useBillingAdjustments();

  const subscription = (planQ.data as Subscription | null) ?? null;
  const usage = (usageQ.data as UsageSummary | null) ?? null;
  const daily = (dailyQ.data as DailyUsageDay[] | null) ?? [];
  const invoices = invoicesQ.data ?? [];
  const overage = (overageQ.data as OverageAlertRow[] | null) ?? [];
  const adjustments = adjQ.data ?? [];

  const initialLoading = planQ.isLoading || usageQ.isLoading;

  // Both of these feed every minute count on the page. If either failed there
  // is no honest figure to print, so nothing that quotes a number renders.
  const coreError = planQ.isError || usageQ.isError;

  if (initialLoading) {
    return (
      <div className="space-y-6">
        <Card>
          <CardContent className="flex items-center justify-center py-16 text-muted-foreground">
            <Loader2 className="mr-2 h-5 w-5 animate-spin" aria-hidden /> Loading billing data…
          </CardContent>
        </Card>
      </div>
    );
  }

  if (coreError) {
    return (
      <div className="space-y-6">
        <ErrorState
          title="Billing data did not load"
          message={`${formatBillingError(planQ.isError ? planQ.error : usageQ.error)} Your plan, minutes and invoices are unchanged — this page just could not read them.`}
          troubleshooting={[
            "Retry below, or reload the page.",
            "If you have just signed in on another tab, sign in again here.",
            "Billing is visible to owners and admins — ask an account owner if you are not one.",
          ]}
          onRetry={() => {
            void planQ.refetch();
            void usageQ.refetch();
          }}
        />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PlanDisplay subscription={subscription} />
      <OverageAlertsCard
        alerts={overage}
        usage={usage}
        failed={overageQ.isError}
        error={overageQ.error}
        onRetry={() => void overageQ.refetch()}
      />
      {topupSlot}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <MinutesTracker usage={usage} />
        <CallStats
          daily={daily}
          loading={dailyQ.isLoading}
          failed={dailyQ.isError}
          error={dailyQ.error}
          onRetry={() => void dailyQ.refetch()}
        />
      </div>
      <UsageSummarySection
        daily={daily}
        loading={dailyQ.isLoading}
        failed={dailyQ.isError}
        error={dailyQ.error}
        onRetry={() => void dailyQ.refetch()}
      />
      <RecentInvoices
        invoices={invoices}
        loading={invoicesQ.isLoading}
        failed={invoicesQ.isError}
        error={invoicesQ.error}
        onRetry={() => void invoicesQ.refetch()}
      />
      <AdjustmentsList
        adjustments={adjustments}
        loading={adjQ.isLoading}
        failed={adjQ.isError}
        error={adjQ.error}
        onRetry={() => void adjQ.refetch()}
      />
    </div>
  );
}

function PlanDisplay({ subscription }: { subscription: Subscription | null }) {
  if (!subscription) {
    return (
      <Card className="relative overflow-hidden border-primary/20 bg-gradient-to-br from-primary/[0.08] via-card to-card">
        <div className="pointer-events-none absolute -right-16 -top-20 h-52 w-52 rounded-full bg-primary/10 blur-3xl" />
        <CardHeader className="relative">
          <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-2xl border border-primary/20 bg-primary/10 text-primary">
            <CreditCard className="h-5 w-5" aria-hidden />
          </div>
          <CardTitle>Choose your calling plan</CardTitle>
          <CardDescription>No active subscription is attached to this workspace yet.</CardDescription>
        </CardHeader>
        <CardContent className="relative">
          <Button asChild>
            <Link href="/billing/plans">Choose a plan <ArrowRight className="ml-1 h-4 w-4" aria-hidden /></Link>
          </Button>
        </CardContent>
      </Card>
    );
  }

  const details = [
    { label: "Plan", value: subscription.plan_name || "No plan selected", icon: Layers3, large: true },
    {
      label: "Billing cycle",
      value: formatDateRange(subscription.current_period_start, subscription.current_period_end),
      icon: CalendarDays,
      large: false,
    },
    {
      label: "Included minutes",
      value: subscription.minutes_state === "unlimited" ? "Unlimited" : subscription.minutes_state === "known" ? subscription.minutes_allocated.toLocaleString() : "Unavailable",
      icon: Phone,
      large: true,
    },
  ];

  return (
    <Card className="relative overflow-hidden border-primary/20 bg-gradient-to-br from-primary/[0.08] via-card to-card shadow-sm">
      <div className="pointer-events-none absolute -right-20 -top-24 h-64 w-64 rounded-full bg-primary/10 blur-3xl" />
      <CardHeader className="relative pb-5">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex items-start gap-3">
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border border-primary/20 bg-primary/10 text-primary shadow-sm">
              <CreditCard className="h-5 w-5" aria-hidden />
            </div>
            <div>
              <div className="mb-1 flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.18em] text-primary">
                <Sparkles className="h-3.5 w-3.5" aria-hidden /> Subscription overview
              </div>
              <CardTitle>Current plan</CardTitle>
              <CardDescription className="mt-1">Purchased offer, billing period, and included allowance.</CardDescription>
            </div>
          </div>
          <div className="self-start">{statusBadge(subscription.status)}</div>
        </div>
      </CardHeader>
      <CardContent className="relative">
        {subscription.purchased_price_option ? <p className="mb-4 font-medium">Purchased offer: {formatPurchasedPrice(subscription.purchased_price_option)}</p> : <p className="mb-4 text-sm text-muted-foreground">Purchased price and interval are not available in the billing record. The current catalogue is not used to infer them.</p>}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {details.map((detail) => (
            <div
              key={detail.label}
              className="group rounded-2xl border border-border/80 bg-background/70 p-4 shadow-sm backdrop-blur-sm transition-colors hover:border-primary/30"
            >
              <div className="flex items-center gap-2 text-xs font-semibold text-muted-foreground">
                <detail.icon className="h-4 w-4 text-primary/80" aria-hidden />
                {detail.label}
              </div>
              <div className={`${detail.large ? "text-xl" : "text-sm leading-6"} mt-2 font-bold tabular-nums text-foreground`}>
                {detail.value}
              </div>
            </div>
          ))}
        </div>

        <div className="mt-5 flex flex-col gap-3 border-t border-border/60 pt-5 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            {subscription.cancel_at_period_end && (
              <span className="font-semibold text-amber-600 dark:text-amber-400">Cancels at period end</span>
            )}
            {subscription.current_period_end && (
              <span>{subscription.cancel_at_period_end ? "Access period ends" : "Current billing period ends"}: {formatDate(subscription.current_period_end)}</span>
            )}
          </div>
          <Button asChild size="sm" className="w-full sm:w-auto">
            <Link href="/billing/plans">Manage plan <ArrowRight className="ml-1 h-4 w-4" aria-hidden /></Link>
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function MinutesTracker({ usage }: { usage: UsageSummary | null }) {
  if (!usage) return <Card><CardContent className="p-4">Usage unavailable.</CardContent></Card>;
  const minutesUsed = usage.total_used;
  const minutesIncluded = usage.allocated;
  const minutesOverage = usage.overage;
  const pct = minutesIncluded > 0 ? Math.min(100, (minutesUsed / minutesIncluded) * 100) : 0;
  const remaining = usage.remaining;
  const barColor = pct >= 90 ? "bg-red-500" : pct >= 75 ? "bg-amber-500" : "bg-emerald-500";

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Phone className="h-5 w-5" aria-hidden /> Minutes Usage</CardTitle>
        <CardDescription>Calendar-month call usage, separate from invoice charges.</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="flex items-end justify-between gap-2">
          <div>
            <div className="text-3xl font-black tabular-nums text-foreground">{minutesUsed.toLocaleString()}</div>
            <div className="text-sm text-muted-foreground">{usage.unlimited ? "minutes used · Unlimited allowance" : `of ${minutesIncluded.toLocaleString()} minutes used`}</div>
          </div>
          <div className="text-right">
              <div className="text-lg font-bold tabular-nums text-foreground">{usage.unlimited ? "Unlimited" : remaining.toLocaleString()}</div>
            <div className="text-xs text-muted-foreground">remaining</div>
          </div>
        </div>
        {!usage.unlimited && <><div role="progressbar" aria-label="Monthly minutes used" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} className="mt-4 h-3 w-full overflow-hidden rounded-full bg-muted/40">
          <div className={`h-full rounded-full transition-all duration-500 ${barColor}`} style={{ width: `${pct}%` }} />
        </div>
        <div className="mt-2 flex justify-between text-xs text-muted-foreground">
          <span>{pct.toFixed(1)}% used</span>
          {minutesOverage > 0 && <span className="font-semibold text-red-600 dark:text-red-400">{minutesOverage} overage minutes</span>}
        </div></>}
      </CardContent>
    </Card>
  );
}

function CallStats({
  daily,
  loading,
  failed,
  error,
  onRetry,
}: {
  daily: DailyUsageDay[];
  loading: boolean;
  failed: boolean;
  error: unknown;
  onRetry: () => void;
}) {
  const totalCalls = daily.reduce((s, d) => s + d.totalCalls, 0);
  const successful = daily.reduce((s, d) => s + d.successfulCalls, 0);
  const failedCalls = daily.reduce((s, d) => s + d.failedCalls, 0);
  const totalSeconds = daily.reduce((s, d) => s + d.secondsUsed, 0);
  const avgDuration = totalCalls > 0 ? Math.round(totalSeconds / totalCalls) : 0;

  const stats = [
    { label: "Total Calls", value: totalCalls.toLocaleString(), icon: Phone },
    { label: "Successful", value: successful.toLocaleString(), icon: CheckCircle },
    { label: "Failed", value: failedCalls.toLocaleString(), icon: XCircle },
    { label: "Average settled time per call", value: `${Math.floor(avgDuration / 60)}m ${avgDuration % 60}s`, icon: Clock },
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><TrendingUp className="h-5 w-5" aria-hidden /> Call Stats (30d)</CardTitle>
        <CardDescription>Rolling 30-day call totals. Settled time includes finalized transfer time.</CardDescription>
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="flex items-center justify-center py-6 text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> Loading call stats…
          </div>
        ) : failed ? (
          // Zeros here would read as "you made no calls". We do not know that.
          <SectionLoadError what="Call stats" error={error} onRetry={onRetry} />
        ) : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {stats.map((s) => (
              <div key={s.label} className="rounded-xl border border-border bg-card/50 p-4 text-center">
                <s.icon className="mx-auto h-5 w-5 text-muted-foreground" aria-hidden />
                <div className="mt-2 text-xl font-bold tabular-nums text-foreground">{s.value}</div>
                <div className="mt-1 text-xs text-muted-foreground">{s.label}</div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function UsageSummarySection({
  daily,
  loading,
  failed,
  error,
  onRetry,
}: {
  daily: DailyUsageDay[];
  loading: boolean;
  failed: boolean;
  error: unknown;
  onRetry: () => void;
}) {
  const max = Math.max(...daily.map((d) => d.minutesUsed), 1);
  // Per-day readout (2026-10): the values used to live only in hover `title`s,
  // which touch, keyboard and screen-reader users can never reach. Each bar is
  // now a real button (aria-label carries the value) and the tapped/focused
  // day is printed inside the chart's existing headroom, so nothing moves.
  const [selectedDay, setSelectedDay] = useState<DailyUsageDay | null>(null);
  const readoutDay = selectedDay ?? (daily.length > 0 ? daily[daily.length - 1] : null);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><TrendingUp className="h-5 w-5" aria-hidden /> Daily Usage</CardTitle>
        <CardDescription>Last 30 days of minutes used</CardDescription>
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="flex items-center justify-center py-8 text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> Loading daily usage…
          </div>
        ) : failed ? (
          <SectionLoadError what="Daily usage" error={error} onRetry={onRetry} />
        ) : daily.length === 0 ? (
          <div className="py-8 text-center text-sm text-muted-foreground">No call activity in the last 30 days.</div>
        ) : (
          <>
            <div className="relative h-32 overflow-hidden rounded-2xl border border-border/70 bg-muted/20 px-3 pb-3 pt-5">
              <div className="pointer-events-none absolute inset-x-3 top-1/3 border-t border-dashed border-border/70" />
              <div className="pointer-events-none absolute inset-x-3 top-2/3 border-t border-dashed border-border/70" />
              {readoutDay ? (
                <p
                  aria-live="polite"
                  data-usage-readout
                  className="pointer-events-none absolute right-3 top-0.5 z-10 text-[11px] tabular-nums text-muted-foreground"
                >
                  {formatDate(readoutDay.date)} · {readoutDay.minutesUsed} min
                </p>
              ) : null}
              <div className="relative flex h-full items-end gap-2">
                {daily.map((d) => {
                  const h = (d.minutesUsed / max) * 100;
                  return (
                    <button
                      key={d.date}
                      type="button"
                      data-usage-bar
                      onClick={() => setSelectedDay(d)}
                      onFocus={() => setSelectedDay(d)}
                      aria-label={`${formatDate(d.date)}: ${d.minutesUsed} minutes used`}
                      title={`${d.date}: ${d.minutesUsed} min`}
                      className="group flex h-full flex-1 items-end focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      <div
                        className="w-full min-w-1 rounded-t-md bg-gradient-to-t from-primary to-primary/55 transition-[height,filter] duration-500 group-hover:brightness-110"
                        style={{ height: `${h}%` }}
                      />
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="flex justify-between text-[10px] text-muted-foreground mt-1">
              <span>{formatDate(daily[0].date)}</span>
              <span>{formatDate(daily[daily.length - 1].date)}</span>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function OverageAlertsCard({
  alerts,
  usage,
  failed,
  error,
  onRetry,
}: {
  alerts: OverageAlertRow[];
  usage: UsageSummary | null;
  failed: boolean;
  error: unknown;
  onRetry: () => void;
}) {
  const warnings: { message: string; severity: "warning" | "critical" }[] = [];

  alerts.forEach((a) => {
    warnings.push({
      message: a.type === "minutes"
         ? `You have exceeded your monthly minutes allowance by ${a.exceededBy.toLocaleString()} minutes. ${a.estimatedCharge === null ? "Additional usage pricing is unavailable; this is not a charge." : `Estimated charge: ${formatMinorMoney(a.estimatedCharge, a.currency, a.currency_exponent)}.`}`
        : `You have exceeded your concurrency limit by ${a.exceededBy}. Additional usage pricing is unavailable; this is not a charge.`,
      severity: a.severity,
    });
  });

  if (usage && usage.allocated > 0) {
    const pct = (usage.total_used / usage.allocated) * 100;
    if (pct >= 85 && pct < 100) {
      warnings.push({
        message: `You have used ${pct.toFixed(0)}% of your included minutes. Review your allowance before starting more calls.`,
        severity: "warning",
      });
    }
  }

  // Hiding the card on a failed fetch would be the silent version of the same
  // bug: no alerts shown reads as "nothing to worry about", and an unread
  // overage alert costs money.
  if (failed) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><AlertTriangle className="h-5 w-5 text-amber-600" aria-hidden /> Alerts</CardTitle>
        </CardHeader>
        <CardContent>
          <SectionLoadError what="Overage alerts" error={error} onRetry={onRetry} />
        </CardContent>
      </Card>
    );
  }

  if (warnings.length === 0) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><AlertTriangle className="h-5 w-5 text-amber-600" aria-hidden /> Alerts</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {warnings.map((w, i) => (
          <div key={i} role="alert" className={`flex items-start gap-3 rounded-xl border px-4 py-3 ${w.severity === "critical" ? "border-red-500/30 bg-red-500/10" : "border-amber-500/30 bg-amber-500/10"}`}>
            <AlertTriangle className={`mt-0.5 h-5 w-5 flex-shrink-0 ${w.severity === "critical" ? "text-red-600 dark:text-red-400" : "text-amber-600 dark:text-amber-400"}`} aria-hidden />
            <div className={`text-sm font-medium ${w.severity === "critical" ? "text-red-800 dark:text-red-300" : "text-amber-800 dark:text-amber-300"}`}>{w.message}</div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function AdjustmentsList({ adjustments, loading, failed, error, onRetry }: {
  adjustments: BillingLedgerEntry[]; loading: boolean; failed: boolean; error: unknown; onRetry: () => void;
}) {
  return <Card><CardHeader><CardTitle>Top-up accounting movements</CardTitle>
    <CardDescription>Signed minute and monetary ledger entries. A reversal is not proof that funds reached a bank.</CardDescription></CardHeader>
    <CardContent>{loading ? <p role="status">Loading accounting movements…</p> : failed ?
      <SectionLoadError what="Accounting movements" error={error} onRetry={onRetry} /> : adjustments.length === 0 ? <p className="text-sm text-muted-foreground">No top-up accounting movements recorded.</p> :
      <div className="overflow-x-auto"><table className="min-w-full text-sm"><caption className="sr-only">Recorded top-up accounting entries</caption>
        <thead><tr className="border-b text-left"><th scope="col" className="p-3">Movement</th><th scope="col" className="p-3">Minutes</th><th scope="col" className="p-3">Amount</th><th scope="col" className="p-3">Order and provider reference</th><th scope="col" className="p-3">Recorded</th></tr></thead>
        <tbody>{adjustments.map((entry) => <tr key={entry.id} className="border-b"><td className="p-3">{entry.kind === "topup" ? "Top-up credit" : entry.kind === "refund" ? "Refund accounting reversal" : entry.kind === "dispute" ? "Dispute accounting reversal" : "Adjustment"}{entry.note && <p className="text-xs text-muted-foreground">{entry.note}</p>}</td>
          <td className="p-3 tabular-nums">{entry.minutes_delta > 0 ? "+" : ""}{entry.minutes_delta.toLocaleString()}</td>
          <td className="p-3 tabular-nums">{formatMinorMoney(entry.amount_cents, entry.currency, entry.currency_exponent)}</td>
          <td className="p-3 break-all"><p>{entry.order_id ?? "Order unavailable"}</p><p className="text-xs text-muted-foreground">{entry.provider_payment_id ?? entry.provider_event_id ?? "Provider reference unavailable"}</p></td>
          <td className="p-3">{formatDate(entry.created_at)}</td></tr>)}</tbody>
      </table></div>}</CardContent></Card>;
}

function RecentInvoices({
  invoices,
  loading,
  failed,
  error,
  onRetry,
}: {
  invoices: InvoiceRow[];
  loading: boolean;
  failed: boolean;
  error: unknown;
  onRetry: () => void;
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2"><FileText className="h-5 w-5" aria-hidden /> Recent Invoices</CardTitle>
            <CardDescription>Your latest billing invoices</CardDescription>
          </div>
          <Button asChild variant="outline" size="sm">
            <Link href="/billing/invoices">Recent invoices <ArrowRight className="ml-1 h-4 w-4" aria-hidden /></Link>
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="flex items-center justify-center py-6 text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> Loading…
          </div>
        ) : failed ? (
          // "No invoices yet" on a failed fetch tells a customer they have
          // never been billed. Say what happened instead.
          <SectionLoadError what="Your invoices" error={error} onRetry={onRetry} />
        ) : invoices.length === 0 ? (
          <div className="py-8 text-center text-sm text-muted-foreground">No invoices yet.</div>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-border bg-card/50">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-muted/30 text-left text-xs font-semibold text-muted-foreground">
                  <th className="px-4 py-3">Invoice</th>
                  <th className="px-4 py-3">Period</th>
                  <th className="px-4 py-3 text-right">Total</th>
                  <th className="px-4 py-3">Status</th>
                </tr>
              </thead>
              <tbody>
                {invoices.slice(0, 3).map((inv) => (
                  <tr key={inv.id} className="border-b border-border last:border-b-0 hover:bg-muted/20 transition-colors">
                    <td className="px-4 py-3">
                      <Link href={`/billing/invoices/${inv.id}`} className="inline-block py-1 -my-1 font-semibold text-foreground hover:underline">
                        {inv.stripe_invoice_id || inv.id.slice(0, 8)}
                      </Link>
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">{formatDate(inv.period_start)} – {formatDate(inv.period_end)}</td>
                    <td className="px-4 py-3 text-right font-semibold tabular-nums text-foreground">{formatMinorMoney(inv.total, inv.currency, inv.currency_exponent)}</td>
                    <td className="px-4 py-3">{invoiceStatusBadge(inv.status)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
