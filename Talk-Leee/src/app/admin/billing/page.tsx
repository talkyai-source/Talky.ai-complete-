"use client";

import Link from "next/link";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ArrowRight, Building2 } from "lucide-react";
import { PARTNER_BILLING } from "@/lib/billing-mock-data";
import type { BillingState } from "@/lib/billing-types";

function formatCurrency(amount: number) {
  return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", minimumFractionDigits: 0 }).format(amount);
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

export default function AdminBillingPage() {
  const totalRevenue = PARTNER_BILLING.reduce((sum, p) => sum + p.totalRevenue, 0);
  const totalMinutes = PARTNER_BILLING.reduce((sum, p) => sum + p.totalMinutesUsed, 0);
  const totalTenants = PARTNER_BILLING.reduce((sum, p) => sum + p.totalTenants, 0);
  const totalOverage = PARTNER_BILLING.reduce((sum, p) => sum + p.totalOverageCharges, 0);

  return (
    <DashboardLayout title="Partner Billing" description="Aggregated billing overview across all white-label partners.">
      <div className="space-y-6">
        {/* Summary KPIs */}
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Card>
            <CardContent className="pt-6 text-center">
              <div className="text-2xl font-black tabular-nums text-foreground">{formatCurrency(totalRevenue)}</div>
              <div className="mt-1 text-xs text-muted-foreground">Total Revenue</div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-6 text-center">
              <div className="text-2xl font-black tabular-nums text-foreground">{totalMinutes.toLocaleString()}</div>
              <div className="mt-1 text-xs text-muted-foreground">Total Minutes Used</div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-6 text-center">
              <div className="text-2xl font-black tabular-nums text-foreground">{totalTenants}</div>
              <div className="mt-1 text-xs text-muted-foreground">Total Tenants</div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-6 text-center">
              <div className="text-2xl font-black tabular-nums text-foreground">{formatCurrency(totalOverage)}</div>
              <div className="mt-1 text-xs text-muted-foreground">Overage Charges</div>
            </CardContent>
          </Card>
        </div>

        {/* Partner Table */}
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="flex items-center gap-2"><Building2 className="h-5 w-5" aria-hidden /> Partners</CardTitle>
                <CardDescription>Usage and billing grouped by white-label partner</CardDescription>
              </div>
              <Button asChild variant="outline" size="sm">
                <Link href="/admin/billing/tenants">View Tenants <ArrowRight className="ml-1 h-4 w-4" aria-hidden /></Link>
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto rounded-xl border border-border bg-card/50">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-muted/30 text-left text-xs font-semibold text-muted-foreground">
                    <th className="px-4 py-3">Partner</th>
                    <th className="px-4 py-3">Tenants</th>
                    <th className="px-4 py-3">Minutes Used / Included</th>
                    <th className="px-4 py-3">Overage</th>
                    <th className="px-4 py-3">Peak Concurrency</th>
                    <th className="px-4 py-3 text-right">Revenue</th>
                    <th className="px-4 py-3 text-right">Overage Charges</th>
                    <th className="px-4 py-3">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {PARTNER_BILLING.map((p) => {
                    const minPct = p.totalMinutesIncluded > 0 ? (p.totalMinutesUsed / p.totalMinutesIncluded) * 100 : 0;
                    return (
                      <tr key={p.partnerId} className="border-b border-border last:border-b-0 hover:bg-muted/20 transition-colors">
                        <td className="px-4 py-3 font-semibold text-foreground">{p.partnerName}</td>
                        <td className="px-4 py-3 tabular-nums text-foreground">{p.activeTenants} / {p.totalTenants}</td>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2">
                            <div className="flex-1">
                              <div className="h-2 w-full overflow-hidden rounded-full bg-muted/40">
                                <div className={`h-full rounded-full ${minPct >= 90 ? "bg-red-500" : minPct >= 75 ? "bg-amber-500" : "bg-emerald-500"}`} style={{ width: `${Math.min(100, minPct)}%` }} />
                              </div>
                            </div>
                            <span className="text-xs tabular-nums text-muted-foreground whitespace-nowrap">{p.totalMinutesUsed.toLocaleString()} / {p.totalMinutesIncluded.toLocaleString()}</span>
                          </div>
                        </td>
                        <td className="px-4 py-3 tabular-nums">
                          {p.totalOverageMinutes > 0
                            ? <span className="text-red-600 dark:text-red-400 font-semibold">{p.totalOverageMinutes.toLocaleString()} min</span>
                            : <span className="text-muted-foreground">—</span>}
                        </td>
                        <td className="px-4 py-3 tabular-nums text-foreground">{p.peakConcurrency}</td>
                        <td className="px-4 py-3 text-right font-semibold tabular-nums text-foreground">{formatCurrency(p.totalRevenue)}</td>
                        <td className="px-4 py-3 text-right tabular-nums">
                          {p.totalOverageCharges > 0
                            ? <span className="font-semibold text-red-600 dark:text-red-400">{formatCurrency(p.totalOverageCharges)}</span>
                            : <span className="text-muted-foreground">—</span>}
                        </td>
                        <td className="px-4 py-3">{billingStateBadge(p.billingState)}</td>
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
