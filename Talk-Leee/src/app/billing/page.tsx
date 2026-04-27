"use client";

import Link from "next/link";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { AlertTriangle, ArrowRight, CreditCard, FileText, TrendingUp, Zap, Phone, CheckCircle, Clock, XCircle } from "lucide-react";
import { CURRENT_TENANT_PLAN, CURRENT_USAGE, DAILY_USAGE, OVERAGE_ALERTS, ADJUSTMENTS, INVOICES } from "@/lib/billing-mock-data";
import type { BillingState, InvoiceStatus, OverageAlert as OverageAlertType, BillingAdjustment } from "@/lib/billing-types";

// ── Formatters ──

function formatCurrency(amount: number) {
  return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", minimumFractionDigits: 2 }).format(amount);
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function formatDateRange(start: string, end: string) {
  const s = new Date(start).toLocaleDateString(undefined, { month: "long", day: "numeric" });
  const e = new Date(end).toLocaleDateString(undefined, { month: "long", day: "numeric" });
  return `${s} – ${e}`;
}

// ── Billing State Badge ──

function billingStateBadge(state: BillingState) {
  const map: Record<BillingState, { label: string; className: string }> = {
    active: { label: "Active", className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" },
    trialing: { label: "Trial", className: "border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-400" },
    past_due: { label: "Past Due", className: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400" },
    grace_period: { label: "Grace Period", className: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400" },
    suspended: { label: "Suspended", className: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400" },
    canceled: { label: "Canceled", className: "border-gray-500/30 bg-gray-500/10 text-gray-700 dark:text-gray-400" },
  };
  const b = map[state];
  return <span className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold ${b.className}`}>{b.label}</span>;
}

function invoiceStatusBadge(status: InvoiceStatus) {
  const map: Record<InvoiceStatus, { label: string; className: string }> = {
    paid: { label: "Paid", className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" },
    open: { label: "Open", className: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400" },
    past_due: { label: "Past Due", className: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400" },
    void: { label: "Void", className: "border-gray-500/30 bg-gray-500/10 text-gray-700 dark:text-gray-400" },
    draft: { label: "Draft", className: "border-gray-500/30 bg-gray-500/10 text-gray-700 dark:text-gray-400" },
  };
  const b = map[status];
  return <span className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold ${b.className}`}>{b.label}</span>;
}

// ── Plan Display ──

function PlanDisplay() {
  const { plan, billingState, billingCycleStart, billingCycleEnd, isYearly, nextInvoiceDate } = CURRENT_TENANT_PLAN;
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2">
              <CreditCard className="h-5 w-5" aria-hidden />
              Current Plan
            </CardTitle>
            <CardDescription>Your active subscription details</CardDescription>
          </div>
          {billingStateBadge(billingState)}
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-xl border border-border bg-card/50 p-4">
            <div className="text-xs font-semibold text-muted-foreground">Plan</div>
            <div className="mt-1 text-xl font-bold text-foreground">{plan.name}</div>
            <div className="mt-1 text-sm text-muted-foreground">{formatCurrency(isYearly ? plan.priceYearly : plan.priceMonthly)}/{isYearly ? "year" : "month"}</div>
          </div>
          <div className="rounded-xl border border-border bg-card/50 p-4">
            <div className="text-xs font-semibold text-muted-foreground">Billing Cycle</div>
            <div className="mt-1 text-sm font-semibold text-foreground">{formatDateRange(billingCycleStart, billingCycleEnd)}</div>
          </div>
          <div className="rounded-xl border border-border bg-card/50 p-4">
            <div className="text-xs font-semibold text-muted-foreground">Included Minutes</div>
            <div className="mt-1 text-xl font-bold text-foreground tabular-nums">{plan.includedMinutes.toLocaleString()}</div>
          </div>
          <div className="rounded-xl border border-border bg-card/50 p-4">
            <div className="text-xs font-semibold text-muted-foreground">Concurrent Calls</div>
            <div className="mt-1 text-xl font-bold text-foreground tabular-nums">{plan.includedConcurrentCalls}</div>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button asChild variant="outline" size="sm">
            <Link href="/billing/plans">Change Plan <ArrowRight className="ml-1 h-4 w-4" aria-hidden /></Link>
          </Button>
          <span className="text-xs text-muted-foreground">Next invoice: {formatDate(nextInvoiceDate)}</span>
        </div>
      </CardContent>
    </Card>
  );
}

// ── Minutes Tracker ──

function MinutesTracker() {
  const { minutesUsed, minutesIncluded, minutesOverage } = CURRENT_USAGE;
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

// ── Concurrency Meter ──

function ConcurrencyMeter() {
  const { peakConcurrency, concurrencyLimit } = CURRENT_USAGE;
  const pct = concurrencyLimit > 0 ? Math.min(100, (peakConcurrency / concurrencyLimit) * 100) : 0;
  const barColor = pct >= 90 ? "bg-red-500" : pct >= 75 ? "bg-amber-500" : "bg-emerald-500";

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Zap className="h-5 w-5" aria-hidden /> Concurrent Calls</CardTitle>
        <CardDescription>Peak concurrent call usage this cycle</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="flex items-end justify-between gap-2">
          <div>
            <div className="text-3xl font-black tabular-nums text-foreground">{peakConcurrency}</div>
            <div className="text-sm text-muted-foreground">peak concurrent calls</div>
          </div>
          <div className="text-right">
            <div className="text-lg font-bold tabular-nums text-foreground">{concurrencyLimit}</div>
            <div className="text-xs text-muted-foreground">limit</div>
          </div>
        </div>
        <div className="mt-4 h-3 w-full overflow-hidden rounded-full bg-muted/40">
          <div className={`h-full rounded-full transition-all duration-500 ${barColor}`} style={{ width: `${pct}%` }} />
        </div>
        <div className="mt-2 flex justify-between text-xs text-muted-foreground">
          <span>{pct.toFixed(0)}% of limit</span>
          <span>{concurrencyLimit - peakConcurrency} slots available</span>
        </div>
      </CardContent>
    </Card>
  );
}

// ── Usage Summary ──

function UsageSummarySection() {
  const u = CURRENT_USAGE;
  const stats = [
    { label: "Total Calls", value: u.totalCalls.toLocaleString(), icon: Phone },
    { label: "Successful", value: u.successfulCalls.toLocaleString(), icon: CheckCircle },
    { label: "Failed", value: u.failedCalls.toLocaleString(), icon: XCircle },
    { label: "Avg Duration", value: `${Math.floor(u.averageCallDuration / 60)}m ${u.averageCallDuration % 60}s`, icon: Clock },
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><TrendingUp className="h-5 w-5" aria-hidden /> Usage Summary</CardTitle>
        <CardDescription>Current billing cycle call statistics</CardDescription>
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
        {/* Daily usage mini-chart */}
        <div className="mt-6">
          <div className="text-xs font-semibold text-muted-foreground mb-2">Daily Minutes (Last 14 Days)</div>
          <div className="flex items-end gap-1 h-20">
            {DAILY_USAGE.map((d, i) => {
              const max = Math.max(...DAILY_USAGE.map((x) => x.minutesUsed));
              const h = max > 0 ? (d.minutesUsed / max) * 100 : 0;
              return (
                <div key={i} className="flex-1 flex flex-col items-center gap-1">
                  <div className="w-full rounded-t bg-primary/70 transition-all" style={{ height: `${h}%` }} title={`${d.minutesUsed} min`} />
                </div>
              );
            })}
          </div>
          <div className="flex justify-between text-[10px] text-muted-foreground mt-1">
            <span>{formatDate(DAILY_USAGE[0].date)}</span>
            <span>{formatDate(DAILY_USAGE[DAILY_USAGE.length - 1].date)}</span>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ── Overage Alerts ──

function OverageAlerts() {
  const alerts: OverageAlertType[] = OVERAGE_ALERTS;
  // Also check current usage for near-limit warnings
  const { minutesUsed, minutesIncluded, peakConcurrency, concurrencyLimit } = CURRENT_USAGE;
  const minutesPct = minutesIncluded > 0 ? (minutesUsed / minutesIncluded) * 100 : 0;
  const concPct = concurrencyLimit > 0 ? (peakConcurrency / concurrencyLimit) * 100 : 0;

  const warnings: { type: string; message: string; severity: "warning" | "critical" }[] = [];

  if (alerts.length > 0) {
    alerts.forEach((a) => {
      warnings.push({
        type: a.type,
        message: a.type === "minutes"
          ? `You have exceeded your monthly minutes limit by ${a.exceededBy.toLocaleString()} minutes. Estimated overage charge: ${formatCurrency(a.estimatedCharge)}.`
          : `You have exceeded your concurrency limit by ${a.exceededBy}. Estimated overage charge: ${formatCurrency(a.estimatedCharge)}.`,
        severity: a.severity,
      });
    });
  }

  if (minutesPct >= 85 && minutesPct < 100) {
    warnings.push({ type: "minutes_warning", message: `You have used ${minutesPct.toFixed(0)}% of your included minutes. Consider upgrading your plan to avoid overage charges.`, severity: "warning" });
  }
  if (concPct >= 85 && concPct < 100) {
    warnings.push({ type: "concurrency_warning", message: `Peak concurrency has reached ${concPct.toFixed(0)}% of your limit. You may experience call rejections if the limit is reached.`, severity: "warning" });
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

// ── Adjustments / Credits ──

function AdjustmentsList() {
  const adjustments = ADJUSTMENTS;
  if (adjustments.length === 0) return null;

  const typeLabel = (t: BillingAdjustment["type"]) => {
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
                  <td className="px-4 py-3">{typeLabel(a.type)}</td>
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

// ── Recent Invoices ──

function RecentInvoices() {
  const invoices = INVOICES.slice(0, 3);
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
        <div className="overflow-x-auto rounded-xl border border-border bg-card/50">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/30 text-left text-xs font-semibold text-muted-foreground">
                <th className="px-4 py-3">Invoice</th>
                <th className="px-4 py-3">Period</th>
                <th className="px-4 py-3">Minutes</th>
                <th className="px-4 py-3 text-right">Total</th>
                <th className="px-4 py-3">Status</th>
              </tr>
            </thead>
            <tbody>
              {invoices.map((inv) => (
                <tr key={inv.id} className="border-b border-border last:border-b-0 hover:bg-muted/20 transition-colors">
                  <td className="px-4 py-3">
                    <Link href={`/billing/invoices/${inv.id}`} className="font-semibold text-foreground hover:underline">{inv.id}</Link>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">{formatDate(inv.billingPeriodStart)} – {formatDate(inv.billingPeriodEnd)}</td>
                  <td className="px-4 py-3 tabular-nums text-foreground">{inv.usedMinutes.toLocaleString()}/{inv.includedMinutes.toLocaleString()}</td>
                  <td className="px-4 py-3 text-right font-semibold tabular-nums text-foreground">{formatCurrency(inv.totalAmount)}</td>
                  <td className="px-4 py-3">{invoiceStatusBadge(inv.status)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

// ── Page ──

export default function BillingPage() {
  return (
    <DashboardLayout title="Billing" description="Manage your subscription, track usage, and view invoices.">
      <div className="space-y-6">
        <PlanDisplay />
        <OverageAlerts />
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <MinutesTracker />
          <ConcurrencyMeter />
        </div>
        <UsageSummarySection />
        <RecentInvoices />
        <AdjustmentsList />
      </div>
    </DashboardLayout>
  );
}
