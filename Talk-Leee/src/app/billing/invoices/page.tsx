"use client";

import Link from "next/link";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ArrowLeft, Download, FileText, Loader2 } from "lucide-react";
import { useBillingInvoices } from "@/lib/billing-api";

type InvoiceRow = {
  id: string;
  stripe_invoice_id?: string;
  amount_due: number;
  amount_paid: number;
  currency: string;
  status: string;
  period_start: string | null;
  period_end: string | null;
  paid_at: string | null;
  due_date: string | null;
  created_at: string;
  hosted_invoice_url?: string | null;
  invoice_pdf?: string | null;
};

function formatCurrency(amountCents: number, currency = "usd") {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: currency.toUpperCase(),
    minimumFractionDigits: 2,
  }).format((amountCents || 0) / 100);
}

function formatDate(iso: string | null | undefined) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function invoiceStatusBadge(status: string) {
  const s = (status || "").toLowerCase();
  const map: Record<string, { label: string; className: string }> = {
    paid: { label: "Paid", className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" },
    open: { label: "Open", className: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400" },
    past_due: { label: "Past Due", className: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400" },
    uncollectible: { label: "Uncollectible", className: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400" },
    void: { label: "Void", className: "border-gray-500/30 bg-gray-500/10 text-gray-700 dark:text-gray-400" },
    draft: { label: "Draft", className: "border-gray-500/30 bg-gray-500/10 text-gray-700 dark:text-gray-400" },
  };
  const b = map[s] || { label: status || "—", className: "border-gray-500/30 bg-gray-500/10 text-gray-700 dark:text-gray-400" };
  return <span className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold ${b.className}`}>{b.label}</span>;
}

export default function InvoicesPage() {
  const query = useBillingInvoices();
  const payload = query.data as { invoices?: InvoiceRow[] } | InvoiceRow[] | null;
  const invoices: InvoiceRow[] = Array.isArray(payload)
    ? payload
    : payload?.invoices ?? [];

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
            {query.isLoading ? (
              <div className="flex items-center justify-center py-12 text-muted-foreground">
                <Loader2 className="mr-2 h-5 w-5 animate-spin" aria-hidden /> Loading invoices…
              </div>
            ) : invoices.length === 0 ? (
              <div className="py-12 text-center text-sm text-muted-foreground">
                No invoices yet. Invoices will appear here after your first billing cycle.
              </div>
            ) : (
              <div className="overflow-x-auto rounded-xl border border-border bg-card/50">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="border-b border-border bg-muted/30 text-left text-xs font-semibold text-muted-foreground">
                      <th className="px-4 py-3">Invoice</th>
                      <th className="px-4 py-3">Billing Period</th>
                      <th className="px-4 py-3 text-right">Amount Due</th>
                      <th className="px-4 py-3 text-right">Paid</th>
                      <th className="px-4 py-3">Status</th>
                      <th className="px-4 py-3">Paid On</th>
                      <th className="px-4 py-3"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {invoices.map((inv) => (
                      <tr key={inv.id} className="border-b border-border last:border-b-0 hover:bg-muted/20 transition-colors">
                        <td className="px-4 py-3">
                          <Link href={`/billing/invoices/${inv.id}`} className="font-semibold text-foreground hover:underline">
                            {inv.stripe_invoice_id || inv.id.slice(0, 8)}
                          </Link>
                        </td>
                        <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">
                          {formatDate(inv.period_start)} – {formatDate(inv.period_end)}
                        </td>
                        <td className="px-4 py-3 text-right font-semibold tabular-nums text-foreground">
                          {formatCurrency(inv.amount_due, inv.currency)}
                        </td>
                        <td className="px-4 py-3 text-right tabular-nums text-muted-foreground">
                          {formatCurrency(inv.amount_paid, inv.currency)}
                        </td>
                        <td className="px-4 py-3">{invoiceStatusBadge(inv.status)}</td>
                        <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">{formatDate(inv.paid_at)}</td>
                        <td className="px-4 py-3">
                          {inv.invoice_pdf ? (
                            <a href={inv.invoice_pdf} target="_blank" rel="noopener noreferrer">
                              <Button variant="ghost" size="sm" title="Download PDF">
                                <Download className="h-4 w-4" aria-hidden />
                              </Button>
                            </a>
                          ) : (
                            <Button variant="ghost" size="sm" title="No PDF available" disabled>
                              <Download className="h-4 w-4 opacity-30" aria-hidden />
                            </Button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </DashboardLayout>
  );
}
