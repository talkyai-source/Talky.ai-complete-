"use client";

import Link from "next/link";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ArrowLeft, Download, FileText } from "lucide-react";
import { INVOICES } from "@/lib/billing-mock-data";
import type { InvoiceStatus } from "@/lib/billing-types";

function formatCurrency(amount: number) {
  return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", minimumFractionDigits: 2 }).format(amount);
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
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

export default function InvoicesPage() {
  return (
    <DashboardLayout title="Invoices" description="View and download your billing invoices.">
      <div className="space-y-6">
        <div className="flex items-center gap-3">
          <Button asChild variant="outline" size="sm">
            <Link href="/billing"><ArrowLeft className="mr-1 h-4 w-4" aria-hidden /> Back to Billing</Link>
          </Button>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><FileText className="h-5 w-5" aria-hidden /> All Invoices</CardTitle>
            <CardDescription>Complete invoice history for your account</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto rounded-xl border border-border bg-card/50">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-muted/30 text-left text-xs font-semibold text-muted-foreground">
                    <th className="px-4 py-3">Invoice</th>
                    <th className="px-4 py-3">Billing Period</th>
                    <th className="px-4 py-3">Plan</th>
                    <th className="px-4 py-3">Minutes Used</th>
                    <th className="px-4 py-3">Overage</th>
                    <th className="px-4 py-3">Peak Concurrency</th>
                    <th className="px-4 py-3 text-right">Total</th>
                    <th className="px-4 py-3">Status</th>
                    <th className="px-4 py-3">Paid</th>
                    <th className="px-4 py-3"></th>
                  </tr>
                </thead>
                <tbody>
                  {INVOICES.map((inv) => (
                    <tr key={inv.id} className="border-b border-border last:border-b-0 hover:bg-muted/20 transition-colors">
                      <td className="px-4 py-3">
                        <Link href={`/billing/invoices/${inv.id}`} className="font-semibold text-foreground hover:underline">{inv.id}</Link>
                      </td>
                      <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">{formatDate(inv.billingPeriodStart)} – {formatDate(inv.billingPeriodEnd)}</td>
                      <td className="px-4 py-3 text-foreground">{inv.planName}</td>
                      <td className="px-4 py-3 tabular-nums text-foreground">{inv.usedMinutes.toLocaleString()} / {inv.includedMinutes.toLocaleString()}</td>
                      <td className="px-4 py-3 tabular-nums">
                        {inv.overageMinutes > 0
                          ? <span className="text-red-600 dark:text-red-400 font-semibold">{inv.overageMinutes.toLocaleString()} min</span>
                          : <span className="text-muted-foreground">—</span>}
                      </td>
                      <td className="px-4 py-3 tabular-nums text-foreground">{inv.peakConcurrentCalls} / {inv.includedConcurrentCalls}</td>
                      <td className="px-4 py-3 text-right font-semibold tabular-nums text-foreground">{formatCurrency(inv.totalAmount)}</td>
                      <td className="px-4 py-3">{invoiceStatusBadge(inv.status)}</td>
                      <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">{inv.paidAt ? formatDate(inv.paidAt) : "—"}</td>
                      <td className="px-4 py-3">
                        <Button variant="ghost" size="sm" title="Download PDF">
                          <Download className="h-4 w-4" aria-hidden />
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      </div>
    </DashboardLayout>
  );
}
