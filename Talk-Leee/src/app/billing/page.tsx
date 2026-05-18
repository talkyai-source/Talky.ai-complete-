"use client";

import Link from "next/link";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { AlertTriangle, ArrowRight, CreditCard, FileText, Loader2, TrendingUp, Phone, CheckCircle, Clock, XCircle } from "lucide-react";
import {
  useBillingPlan,
  useBillingUsage,
  useDailyUsage,
  useBillingInvoices,
  useOverageAlerts,
  useBillingAdjustments,
} from "@/lib/billing-api";

type Subscription = {
  status: string;
  plan_id?: string | null;
  plan_name?: string | null;
  current_period_start?: string | null;
  current_period_end?: string | null;
  cancel_at_period_end?: boolean;
  minutes_allocated: number;
  minutes_used: number;
  minutes_remaining: number;
};

type UsageSummary = {
  usage_type: string;
  total_used: number;
  allocated: number;
  remaining: number;
  overage: number;
};

type DailyUsageDay = {
  date: string;
  minutesUsed: number;
  totalCalls: number;
  successfulCalls: number;
  failedCalls: number;
};

type InvoiceRow = {
  id: string;
  stripe_invoice_id?: string;
  amount_due: number;
  currency: string;
  status: string;
  period_start: string | null;
  period_end: string | null;
  created_at: string;
};

type OverageAlertRow = {
  type: "minutes" | "concurrency";
  currentUsage: number;
  limit: number;
  exceededBy: number;
  estimatedCharge: number;
  severity: "warning" | "critical";
};

type AdjustmentRow = {
  id: string;
  type: "credit" | "debit" | "refund" | "promo";
  description: string;
  amount: number;
  appliedAt: string;
  reason: string;
};

function formatCurrency(amount: number) {
  return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", minimumFractionDigits: 2 }).format(amount);
}

function formatCents(cents: number, currency = "usd") {
  return new Intl.NumberFormat(undefined, { style: "currency", currency: currency.toUpperCase(), minimumFractionDigits: 2 }).format((cents || 0) / 100);
}

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

function statusBadge(state: string) {
  const s = (state || "").toLowerCase();
  const map: Record<string, { label: string; className: string }> = {
    active: { label: "Active", className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" },
    trialing: { label: "Trial", className: "border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-400" },
    past_due: { label: "Past Due", className: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400" },
    inactive: { label: "Inactive", className: "border-gray-500/30 bg-gray-500/10 text-gray-700 dark:text-gray-400" },
    canceled: { label: "Canceled", className: "border-gray-500/30 bg-gray-500/10 text-gray-700 dark:text-gray-400" },
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

export default function BillingPage() {
  const planQ = useBillingPlan();
  const usageQ = useBillingUsage();
  const dailyQ = useDailyUsage();
  const invoicesQ = useBillingInvoices();
  const overageQ = useOverageAlerts();
  const adjQ = useBillingAdjustments();

  const subscription = (planQ.data as Subscription | null) ?? null;
  const usage = (usageQ.data as UsageSummary | null) ?? null;
  const daily = (dailyQ.data as DailyUsageDay[] | null) ?? [];
  const invoicesPayload = invoicesQ.data as { invoices?: InvoiceRow[] } | InvoiceRow[] | null;
  const invoices: InvoiceRow[] = Array.isArray(invoicesPayload) ? invoicesPayload : invoicesPayload?.invoices ?? [];
  const overage = (overageQ.data as OverageAlertRow[] | null) ?? [];
  const adjustments = (adjQ.data as AdjustmentRow[] | null) ?? [];

  const initialLoading = planQ.isLoading || usageQ.isLoading;

  return (
    <DashboardLayout title="Billing" description="Manage your subscription, track usage, and view invoices.">
      <div className="space-y-6">
        {initialLoading ? (
          <Card>
            <CardContent className="flex items-center justify-center py-16 text-muted-foreground">
              <Loader2 className="mr-2 h-5 w-5 animate-spin" aria-hidden /> Loading billing data…
            </CardContent>
          </Card>
        ) : (
          <>
            <PlanDisplay subscription={subscription} />
            <OverageAlertsCard alerts={overage} usage={usage} />
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
              <MinutesTracker subscription={subscription} usage={usage} />
              <CallStats daily={daily} />
            </div>
            <UsageSummarySection daily={daily} />
            <RecentInvoices invoices={invoices} loading={invoicesQ.isLoading} />
            <AdjustmentsList adjustments={adjustments} />
          </>
        )}
      </div>
    </DashboardLayout>
  );
}

function PlanDisplay({ subscription }: { subscription: Subscription | null }) {
  if (!subscription) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><CreditCard className="h-5 w-5" aria-hidden /> Current Plan</CardTitle>
          <CardDescription>No active subscription</CardDescription>
        </CardHeader>
        <CardContent>
          <Button asChild>
            <Link href="/billing/plans">Choose a plan <ArrowRight className="ml-1 h-4 w-4" aria-hidden /></Link>
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2"><CreditCard className="h-5 w-5" aria-hidden /> Current Plan</CardTitle>
            <CardDescription>Your active subscription details</CardDescription>
          </div>
          {statusBadge(subscription.status)}
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-xl border border-border bg-card/50 p-4">
            <div className="text-xs font-semibold text-muted-foreground">Plan</div>
            <div className="mt-1 text-xl font-bold text-foreground">{subscription.plan_name || "—"}</div>
          </div>
          <div className="rounded-xl border border-border bg-card/50 p-4">
            <div className="text-xs font-semibold text-muted-foreground">Billing Cycle</div>
            <div className="mt-1 text-sm font-semibold text-foreground">
              {formatDateRange(subscription.current_period_start, subscription.current_period_end)}
            </div>
          </div>
          <div className="rounded-xl border border-border bg-card/50 p-4">
            <div className="text-xs font-semibold text-muted-foreground">Included Minutes</div>
            <div className="mt-1 text-xl font-bold text-foreground tabular-nums">{subscription.minutes_allocated.toLocaleString()}</div>
          </div>
          <div className="rounded-xl border border-border bg-card/50 p-4">
            <div className="text-xs font-semibold text-muted-foreground">Minutes Remaining</div>
            <div className="mt-1 text-xl font-bold text-foreground tabular-nums">{subscription.minutes_remaining.toLocaleString()}</div>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button asChild variant="outline" size="sm">
            <Link href="/billing/plans">Change Plan <ArrowRight className="ml-1 h-4 w-4" aria-hidden /></Link>
          </Button>
          {subscription.cancel_at_period_end && (
            <span className="text-xs text-amber-600 dark:text-amber-400">Cancels at period end</span>
          )}
          {subscription.current_period_end && (
            <span className="text-xs text-muted-foreground">Next invoice: {formatDate(subscription.current_period_end)}</span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function MinutesTracker({ subscription, usage }: { subscription: Subscription | null; usage: UsageSummary | null }) {
  const minutesUsed = subscription?.minutes_used ?? usage?.total_used ?? 0;
  const minutesIncluded = subscription?.minutes_allocated ?? usage?.allocated ?? 0;
  const minutesOverage = usage?.overage ?? Math.max(0, minutesUsed - minutesIncluded);
  const pct = minutesIncluded > 0 ? Math.min(100, (minutesUsed / minutesIncluded) * 100) : 0;
  const remaining = Math.max(0, minutesIncluded - minutesUsed);
  const barColor = pct >= 90 ? "bg-red-500" : pct >= 75 ? "bg-amber-500" : "bg-emerald-500";

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Phone className="h-5 w-5" aria-hidden /> Minutes Usage</CardTitle>
        <CardDescription>Track your voice minutes consumption</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="flex items-end justify-between gap-2">
          <div>
            <div className="text-3xl font-black tabular-nums text-foreground">{minutesUsed.toLocaleString()}</div>
            <div className="text-sm text-muted-foreground">of {minutesIncluded.toLocaleString()} minutes used</div>
          </div>
          <div className="text-right">
            <div className="text-lg font-bold tabular-nums text-foreground">{remaining.toLocaleString()}</div>
            <div className="text-xs text-muted-foreground">remaining</div>
          </div>
        </div>
        <div className="mt-4 h-3 w-full overflow-hidden rounded-full bg-muted/40">
          <div className={`h-full rounded-full transition-all duration-500 ${barColor}`} style={{ width: `${pct}%` }} />
        </div>
        <div className="mt-2 flex justify-between text-xs text-muted-foreground">
          <span>{pct.toFixed(1)}% used</span>
          {minutesOverage > 0 && <span className="font-semibold text-red-600 dark:text-red-400">{minutesOverage} overage minutes</span>}
        </div>
      </CardContent>
    </Card>
  );
}

function CallStats({ daily }: { daily: DailyUsageDay[] }) {
  const totalCalls = daily.reduce((s, d) => s + d.totalCalls, 0);
  const successful = daily.reduce((s, d) => s + d.successfulCalls, 0);
  const failed = daily.reduce((s, d) => s + d.failedCalls, 0);
  const totalSeconds = daily.reduce((s, d) => s + d.minutesUsed * 60, 0);
  const avgDuration = totalCalls > 0 ? Math.round(totalSeconds / totalCalls) : 0;

  const stats = [
    { label: "Total Calls", value: totalCalls.toLocaleString(), icon: Phone },
    { label: "Successful", value: successful.toLocaleString(), icon: CheckCircle },
    { label: "Failed", value: failed.toLocaleString(), icon: XCircle },
    { label: "Avg Duration", value: `${Math.floor(avgDuration / 60)}m ${avgDuration % 60}s`, icon: Clock },
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><TrendingUp className="h-5 w-5" aria-hidden /> Call Stats (30d)</CardTitle>
        <CardDescription>Rolling 30-day call totals</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {stats.map((s) => (
            <div key={s.label} className="rounded-xl border border-border bg-card/50 p-4 text-center">
              <s.icon className="mx-auto h-5 w-5 text-muted-foreground" aria-hidden />
              <div className="mt-2 text-xl font-bold tabular-nums text-foreground">{s.value}</div>
              <div className="mt-1 text-xs text-muted-foreground">{s.label}</div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function UsageSummarySection({ daily }: { daily: DailyUsageDay[] }) {
  if (daily.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><TrendingUp className="h-5 w-5" aria-hidden /> Daily Usage</CardTitle>
          <CardDescription>Last 30 days of minutes used</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="py-8 text-center text-sm text-muted-foreground">No call activity in the last 30 days.</div>
        </CardContent>
      </Card>
    );
  }

  const max = Math.max(...daily.map((d) => d.minutesUsed), 1);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><TrendingUp className="h-5 w-5" aria-hidden /> Daily Usage</CardTitle>
        <CardDescription>Last 30 days of minutes used</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="flex items-end gap-1 h-24">
          {daily.map((d) => {
            const h = (d.minutesUsed / max) * 100;
            return (
              <div key={d.date} className="flex-1 flex flex-col items-center gap-1" title={`${d.date}: ${d.minutesUsed} min`}>
                <div className="w-full rounded-t bg-primary/70 transition-all" style={{ height: `${h}%` }} />
              </div>
            );
          })}
        </div>
        <div className="flex justify-between text-[10px] text-muted-foreground mt-1">
          <span>{formatDate(daily[0].date)}</span>
          <span>{formatDate(daily[daily.length - 1].date)}</span>
        </div>
      </CardContent>
    </Card>
  );
}

function OverageAlertsCard({ alerts, usage }: { alerts: OverageAlertRow[]; usage: UsageSummary | null }) {
  const warnings: { message: string; severity: "warning" | "critical" }[] = [];

  alerts.forEach((a) => {
    warnings.push({
      message: a.type === "minutes"
        ? `You have exceeded your monthly minutes limit by ${a.exceededBy.toLocaleString()} minutes. Estimated overage charge: ${formatCurrency(a.estimatedCharge)}.`
        : `You have exceeded your concurrency limit by ${a.exceededBy}. Estimated overage charge: ${formatCurrency(a.estimatedCharge)}.`,
      severity: a.severity,
    });
  });

  if (usage && usage.allocated > 0) {
    const pct = (usage.total_used / usage.allocated) * 100;
    if (pct >= 85 && pct < 100) {
      warnings.push({
        message: `You have used ${pct.toFixed(0)}% of your included minutes. Consider upgrading your plan to avoid overage charges.`,
        severity: "warning",
      });
    }
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

function AdjustmentsList({ adjustments }: { adjustments: AdjustmentRow[] }) {
  if (adjustments.length === 0) return null;

  const typeBadge = (t: string) => {
    const m: Record<string, { label: string; className: string }> = {
      credit: { label: "Credit", className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" },
      refund: { label: "Refund", className: "border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-400" },
      debit: { label: "Charge", className: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400" },
      promo: { label: "Promo", className: "border-purple-500/30 bg-purple-500/10 text-purple-700 dark:text-purple-400" },
    };
    const b = m[t] || m.debit;
    return <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold ${b.className}`}>{b.label}</span>;
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Adjustments &amp; Credits</CardTitle>
        <CardDescription>Corrections and credits applied to your account</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto rounded-xl border border-border bg-card/50">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/30 text-left text-xs font-semibold text-muted-foreground">
                <th className="px-4 py-3">Type</th>
                <th className="px-4 py-3">Description</th>
                <th className="px-4 py-3">Reason</th>
                <th className="px-4 py-3 text-right">Amount</th>
                <th className="px-4 py-3">Date</th>
              </tr>
            </thead>
            <tbody>
              {adjustments.map((a) => (
                <tr key={a.id} className="border-b border-border last:border-b-0">
                  <td className="px-4 py-3">{typeBadge(a.type)}</td>
                  <td className="px-4 py-3 font-medium text-foreground">{a.description}</td>
                  <td className="px-4 py-3 text-muted-foreground max-w-[200px] truncate">{a.reason}</td>
                  <td className={`px-4 py-3 text-right font-semibold tabular-nums ${a.amount < 0 ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}`}>
                    {a.amount < 0 ? "-" : "+"}{formatCurrency(Math.abs(a.amount))}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">{formatDate(a.appliedAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

function RecentInvoices({ invoices, loading }: { invoices: InvoiceRow[]; loading: boolean }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2"><FileText className="h-5 w-5" aria-hidden /> Recent Invoices</CardTitle>
            <CardDescription>Your latest billing invoices</CardDescription>
          </div>
          <Button asChild variant="outline" size="sm">
            <Link href="/billing/invoices">View All <ArrowRight className="ml-1 h-4 w-4" aria-hidden /></Link>
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="flex items-center justify-center py-6 text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> Loading…
          </div>
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
                      <Link href={`/billing/invoices/${inv.id}`} className="font-semibold text-foreground hover:underline">
                        {inv.stripe_invoice_id || inv.id.slice(0, 8)}
                      </Link>
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">{formatDate(inv.period_start)} – {formatDate(inv.period_end)}</td>
                    <td className="px-4 py-3 text-right font-semibold tabular-nums text-foreground">{formatCents(inv.amount_due, inv.currency)}</td>
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
