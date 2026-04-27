"use client";

import Link from "next/link";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ArrowLeft, Users } from "lucide-react";
import { TENANT_BILLING } from "@/lib/billing-mock-data";
import type { BillingState, InvoiceStatus } from "@/lib/billing-types";

function formatCurrency(amount: number) {
  return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", minimumFractionDigits: 2 }).format(amount);
}

function billingStateBadge(state: BillingState) {
  const map: Record<BillingState, { label: string; className: string }> = {
    active: { label: "Active", className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" },
    trialing: { label: "Trial", className: "border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-400" },
    past_due: { label: "Past Due", className: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400" },
    grace_period: { label: "Grace", className: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400" },
    suspended: { label: "Suspended", className: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400" },
    canceled: { label: "Canceled", className: "border-gray-500/30 bg-gray-500/10 text-gray-700 dark:text-gray-400" },
  };
  const b = map[state];
  return <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold ${b.className}`}>{b.label}</span>;
}

function paymentBadge(status?: InvoiceStatus) {
  if (!status) return <span className="text-muted-foreground text-xs">—</span>;
  const map: Record<InvoiceStatus, { label: string; className: string }> = {
    paid: { label: "Paid", className: "text-emerald-600 dark:text-emerald-400" },
    open: { label: "Open", className: "text-amber-600 dark:text-amber-400" },
    past_due: { label: "Overdue", className: "text-red-600 dark:text-red-400" },
    void: { label: "Void", className: "text-gray-500" },
    draft: { label: "Draft", className: "text-gray-500" },
  };
  const b = map[status];
  return <span className={`text-xs font-semibold ${b.className}`}>{b.label}</span>;
}

export default function TenantBillingPage() {
  return (
    <DashboardLayout title="Tenant Billing" description="Usage breakdown per customer/sub-tenant.">
      <div className="space-y-6">
        <div className="flex items-center gap-3">
          <Button asChild variant="outline" size="sm">
            <Link href="/admin/billing"><ArrowLeft className="mr-1 h-4 w-4" aria-hidden /> Partner Overview</Link>
          </Button>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><Users className="h-5 w-5" aria-hidden /> Tenant Breakdown</CardTitle>
            <CardDescription>Individual tenant billing and usage details</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto rounded-xl border border-border bg-card/50">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-muted/30 text-left text-xs font-semibold text-muted-foreground">
                    <th className="px-4 py-3">Tenant</th>
                    <th className="px-4 py-3">Plan</th>
                    <th className="px-4 py-3">Minutes Used / Included</th>
                    <th className="px-4 py-3">Overage</th>
                    <th className="px-4 py-3">Concurrency</th>
                    <th className="px-4 py-3 text-right">Charges</th>
                    <th className="px-4 py-3">Status</th>
                    <th className="px-4 py-3">Last Payment</th>
                  </tr>
                </thead>
                <tbody>
                  {TENANT_BILLING.map((t) => {
                    const minPct = t.minutesIncluded > 0 ? (t.minutesUsed / t.minutesIncluded) * 100 : 0;
                    const concPct = t.concurrencyLimit > 0 ? (t.peakConcurrency / t.concurrencyLimit) * 100 : 0;
                    return (
                      <tr key={t.tenantId} className="border-b border-border last:border-b-0 hover:bg-muted/20 transition-colors">
                        <td className="px-4 py-3">
                          <div className="font-semibold text-foreground">{t.tenantName}</div>
                          {t.partnerId && <div className="text-xs text-muted-foreground">{t.partnerId}</div>}
                        </td>
                        <td className="px-4 py-3 text-foreground">{t.planName}</td>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2">
                            <div className="flex-1 min-w-[60px]">
                              <div className="h-2 w-full overflow-hidden rounded-full bg-muted/40">
                                <div className={`h-full rounded-full ${minPct >= 100 ? "bg-red-500" : minPct >= 75 ? "bg-amber-500" : "bg-emerald-500"}`} style={{ width: `${Math.min(100, minPct)}%` }} />
                              </div>
                            </div>
                            <span className="text-xs tabular-nums text-muted-foreground whitespace-nowrap">{t.minutesUsed.toLocaleString()} / {t.minutesIncluded.toLocaleString()}</span>
                          </div>
                        </td>
                        <td className="px-4 py-3 tabular-nums">
                          {t.overageMinutes > 0
                            ? <span className="text-red-600 dark:text-red-400 font-semibold">{t.overageMinutes.toLocaleString()} min</span>
                            : <span className="text-muted-foreground">—</span>}
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2">
                            <div className="flex-1 min-w-[40px]">
                              <div className="h-2 w-full overflow-hidden rounded-full bg-muted/40">
                                <div className={`h-full rounded-full ${concPct >= 90 ? "bg-red-500" : concPct >= 75 ? "bg-amber-500" : "bg-emerald-500"}`} style={{ width: `${Math.min(100, concPct)}%` }} />
                              </div>
                            </div>
                            <span className="text-xs tabular-nums text-muted-foreground whitespace-nowrap">{t.peakConcurrency} / {t.concurrencyLimit}</span>
                          </div>
                        </td>
                        <td className="px-4 py-3 text-right font-semibold tabular-nums text-foreground">{formatCurrency(t.totalCharges)}</td>
                        <td className="px-4 py-3">{billingStateBadge(t.billingState)}</td>
                        <td className="px-4 py-3">
                          <div className="flex flex-col">
                            {t.lastPaymentDate && <span className="text-xs text-muted-foreground">{t.lastPaymentDate}</span>}
                            {paymentBadge(t.lastPaymentStatus)}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      </div>
    </DashboardLayout>
  );
}
