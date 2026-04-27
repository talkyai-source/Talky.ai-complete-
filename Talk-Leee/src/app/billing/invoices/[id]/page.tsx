"use client";

import { use } from "react";
import Link from "next/link";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ArrowLeft, Download, FileText, Printer } from "lucide-react";
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

export default function InvoiceDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const invoice = INVOICES.find((inv) => inv.id === id);

  if (!invoice) {
    return (
      <DashboardLayout title="Invoice Not Found">
        <Card>
          <CardContent className="py-10 text-center">
            <div className="text-lg font-semibold text-foreground">Invoice not found</div>
            <div className="mt-2 text-sm text-muted-foreground">The invoice &quot;{id}&quot; does not exist.</div>
            <Button asChild className="mt-4"><Link href="/billing/invoices">Back to Invoices</Link></Button>
          </CardContent>
        </Card>
      </DashboardLayout>
    );
  }

  const adjustmentTotal = invoice.adjustments.reduce((sum, a) => sum + a.amount, 0);

  return (
    <DashboardLayout title={`Invoice ${invoice.id}`} description={`Billing period: ${formatDate(invoice.billingPeriodStart)} – ${formatDate(invoice.billingPeriodEnd)}`}>
      <div className="space-y-6">
        <div className="flex flex-wrap items-center gap-3">
          <Button asChild variant="outline" size="sm">
            <Link href="/billing/invoices"><ArrowLeft className="mr-1 h-4 w-4" aria-hidden /> All Invoices</Link>
          </Button>
          <div className="flex-1" />
          <Button variant="outline" size="sm" onClick={() => window.print()}>
            <Printer className="mr-1 h-4 w-4" aria-hidden /> Print
          </Button>
          <Button variant="outline" size="sm">
            <Download className="mr-1 h-4 w-4" aria-hidden /> Download PDF
          </Button>
        </div>

        {/* Invoice Header */}
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="flex items-center gap-2"><FileText className="h-5 w-5" aria-hidden /> {invoice.id}</CardTitle>
                <CardDescription>{invoice.planName} Plan</CardDescription>
              </div>
              {invoiceStatusBadge(invoice.status)}
            </div>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <div>
                <div className="text-xs font-semibold text-muted-foreground">Billing Period</div>
                <div className="mt-1 text-sm font-semibold text-foreground">{formatDate(invoice.billingPeriodStart)} – {formatDate(invoice.billingPeriodEnd)}</div>
              </div>
              <div>
                <div className="text-xs font-semibold text-muted-foreground">Due Date</div>
                <div className="mt-1 text-sm font-semibold text-foreground">{formatDate(invoice.dueDate)}</div>
              </div>
              <div>
                <div className="text-xs font-semibold text-muted-foreground">Payment Date</div>
                <div className="mt-1 text-sm font-semibold text-foreground">{invoice.paidAt ? formatDate(invoice.paidAt) : "—"}</div>
              </div>
              <div>
                <div className="text-xs font-semibold text-muted-foreground">Total Amount</div>
                <div className="mt-1 text-xl font-black tabular-nums text-foreground">{formatCurrency(invoice.totalAmount)}</div>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Usage Breakdown */}
        <Card>
          <CardHeader>
            <CardTitle>Usage Breakdown</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <div className="rounded-xl border border-border bg-card/50 p-4">
                <div className="text-xs font-semibold text-muted-foreground">Included Minutes</div>
                <div className="mt-1 text-lg font-bold tabular-nums text-foreground">{invoice.includedMinutes.toLocaleString()}</div>
              </div>
              <div className="rounded-xl border border-border bg-card/50 p-4">
                <div className="text-xs font-semibold text-muted-foreground">Used Minutes</div>
                <div className="mt-1 text-lg font-bold tabular-nums text-foreground">{invoice.usedMinutes.toLocaleString()}</div>
              </div>
              <div className="rounded-xl border border-border bg-card/50 p-4">
                <div className="text-xs font-semibold text-muted-foreground">Overage Minutes</div>
                <div className={`mt-1 text-lg font-bold tabular-nums ${invoice.overageMinutes > 0 ? "text-red-600 dark:text-red-400" : "text-foreground"}`}>
                  {invoice.overageMinutes > 0 ? `+${invoice.overageMinutes.toLocaleString()}` : "0"}
                </div>
              </div>
              <div className="rounded-xl border border-border bg-card/50 p-4">
                <div className="text-xs font-semibold text-muted-foreground">Peak Concurrent Calls</div>
                <div className="mt-1 text-lg font-bold tabular-nums text-foreground">{invoice.peakConcurrentCalls} / {invoice.includedConcurrentCalls}</div>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Line Items */}
        <Card>
          <CardHeader>
            <CardTitle>Line Items</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto rounded-xl border border-border bg-card/50">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-muted/30 text-left text-xs font-semibold text-muted-foreground">
                    <th className="px-4 py-3">Description</th>
                    <th className="px-4 py-3 text-right">Quantity</th>
                    <th className="px-4 py-3 text-right">Unit Price</th>
                    <th className="px-4 py-3 text-right">Total</th>
                  </tr>
                </thead>
                <tbody>
                  {invoice.lineItems.map((item, i) => (
                    <tr key={i} className="border-b border-border last:border-b-0">
                      <td className="px-4 py-3 font-medium text-foreground">{item.description}</td>
                      <td className="px-4 py-3 text-right tabular-nums text-muted-foreground">{item.quantity > 0 ? item.quantity.toLocaleString() : "—"}</td>
                      <td className="px-4 py-3 text-right tabular-nums text-muted-foreground">{item.unitPrice !== 0 ? formatCurrency(item.unitPrice) : "—"}</td>
                      <td className={`px-4 py-3 text-right font-semibold tabular-nums ${item.total < 0 ? "text-emerald-600 dark:text-emerald-400" : "text-foreground"}`}>
                        {item.total !== 0 ? formatCurrency(item.total) : "Included"}
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  {invoice.adjustments.length > 0 && (
                    <tr className="border-t border-border">
                      <td colSpan={3} className="px-4 py-2 text-right text-xs font-semibold text-muted-foreground">Adjustments/Credits</td>
                      <td className={`px-4 py-2 text-right font-semibold tabular-nums ${adjustmentTotal < 0 ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}`}>
                        {formatCurrency(adjustmentTotal)}
                      </td>
                    </tr>
                  )}
                  {invoice.tax > 0 && (
                    <tr className="border-t border-border">
                      <td colSpan={3} className="px-4 py-2 text-right text-xs font-semibold text-muted-foreground">Tax</td>
                      <td className="px-4 py-2 text-right font-semibold tabular-nums text-foreground">{formatCurrency(invoice.tax)}</td>
                    </tr>
                  )}
                  <tr className="border-t-2 border-border bg-muted/20">
                    <td colSpan={3} className="px-4 py-3 text-right font-bold text-foreground">Total Due</td>
                    <td className="px-4 py-3 text-right text-lg font-black tabular-nums text-foreground">{formatCurrency(invoice.totalAmount)}</td>
                  </tr>
                </tfoot>
              </table>
            </div>
          </CardContent>
        </Card>
      </div>
    </DashboardLayout>
  );
}
