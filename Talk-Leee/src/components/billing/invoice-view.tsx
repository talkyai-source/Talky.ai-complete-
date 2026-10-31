"use client";

import Link from "next/link";
import { ArrowLeft, Download, ExternalLink, Loader2, Printer } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/states/page-states";
import { useBillingInvoice, useBillingInvoices } from "@/lib/billing-api";
import { formatMinorMoney, type BillingInvoice } from "@/lib/billing-read";
import { formatBillingError } from "@/components/billing/billing-overview";

export function BillingSupport({ reference }: { reference?: string }) {
  return <p className="text-sm text-muted-foreground">
    For a billing or refund review, <a className="underline underline-offset-4" href={`mailto:billing@talkleeai.com${reference ? `?subject=${encodeURIComponent(`Billing review: ${reference}`)}` : ""}`}>contact billing@talkleeai.com</a> and include your invoice or order reference.
    {" "}A request does not confirm a refund or when funds reach your bank.
  </p>;
}

export function invoiceDate(value: string | null): string {
  return value ? new Date(value).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "Unavailable";
}

export function InvoiceStatus({ status }: { status: string }) {
  return <span className="inline-flex rounded-full border px-3 py-1 text-xs font-semibold capitalize">{status.replaceAll("_", " ")}</span>;
}

export function ProviderRefundFacts({ refunds, detailStatus, capturedAt }: {
  refunds: { id: string; amount: number | null; currency: string | null; currency_exponent: number | null; status: string | null }[] | null;
  detailStatus: "complete" | "partial" | "unavailable"; capturedAt: string | null;
}) {
  return <section className="space-y-2 text-sm" aria-label="Provider refund observations">
    <h3 className="font-semibold">Provider refund status</h3>
    <p className="text-xs text-muted-foreground">{capturedAt ? `Captured ${new Date(capturedAt).toLocaleString()}.` : "Capture time unavailable."} Provider status does not confirm when funds reach a bank.</p>
    {detailStatus !== "complete" && <p>Refund information is {detailStatus}.</p>}
    {refunds === null || (refunds.length === 0 && detailStatus !== "complete") ? <p>Refund entries unavailable or incomplete.</p> : refunds.length === 0 ? <p>No refund entries in the captured records.</p> : <ul className="space-y-1">{refunds.map((refund) => <li key={refund.id} className="break-all">{refund.id} · {formatMinorMoney(refund.amount, refund.currency, refund.currency_exponent)} · Provider status: {refund.status ?? "Unavailable"}</li>)}</ul>}
  </section>;
}

function Documents({ invoice }: { invoice: BillingInvoice }) {
  return <div className="flex flex-wrap gap-2">
    {invoice.hosted_invoice_url && <Button asChild variant="outline" size="sm"><a href={invoice.hosted_invoice_url} target="_blank" rel="noopener noreferrer"><ExternalLink className="mr-1 h-4 w-4" aria-hidden /> Provider invoice</a></Button>}
    {invoice.invoice_pdf ? <Button asChild variant="outline" size="sm"><a href={invoice.invoice_pdf} target="_blank" rel="noopener noreferrer"><Download className="mr-1 h-4 w-4" aria-hidden /> Download PDF</a></Button> : <span className="self-center text-sm text-muted-foreground">Provider PDF unavailable</span>}
  </div>;
}

export function BillingInvoiceList() {
  const query = useBillingInvoices();
  return <div className="space-y-6">
    <Button asChild variant="outline" size="sm"><Link href="/billing"><ArrowLeft className="mr-1 h-4 w-4" aria-hidden /> Back to Billing</Link></Button>
    <Card><CardHeader><CardTitle>Recent invoices</CardTitle><CardDescription>Up to 10 most recent saved invoices for this account.</CardDescription></CardHeader>
      <CardContent>{query.isLoading ? <p role="status"><Loader2 className="mr-2 inline h-4 w-4 animate-spin" aria-hidden /> Loading invoices…</p> : query.isError ?
        <ErrorState title="Invoices did not load" message={formatBillingError(query.error)} onRetry={() => void query.refetch()} /> : query.data?.length === 0 ? <p>No invoices recorded.</p> :
          <div className="overflow-x-auto"><table className="min-w-full text-sm"><caption className="sr-only">Recent saved invoice amounts and status</caption>
            <thead><tr className="border-b text-left"><th scope="col" className="p-3">Invoice</th><th scope="col" className="p-3">Period</th><th scope="col" className="p-3">Invoice total</th><th scope="col" className="p-3">Paid</th><th scope="col" className="p-3">Remaining</th><th scope="col" className="p-3">Status</th></tr></thead>
            <tbody>{query.data?.map((invoice) => <tr key={invoice.id} className="border-b">
              <td className="p-3"><Link className="inline-block py-1 -my-1 font-semibold underline" href={`/billing/invoices/${invoice.id}`}>{invoice.invoice_number ?? invoice.stripe_invoice_id ?? invoice.id}</Link></td>
              <td className="p-3">{invoiceDate(invoice.period_start)} – {invoiceDate(invoice.period_end)}</td>
              <td className="p-3">{formatMinorMoney(invoice.total, invoice.currency, invoice.currency_exponent)}</td>
              <td className="p-3">{formatMinorMoney(invoice.amount_paid, invoice.currency, invoice.currency_exponent)}</td>
              <td className="p-3">{formatMinorMoney(invoice.amount_remaining, invoice.currency, invoice.currency_exponent)}</td>
              <td className="p-3"><InvoiceStatus status={invoice.status} /></td>
            </tr>)}</tbody></table></div>}
      </CardContent></Card>
    <BillingSupport />
  </div>;
}

export function BillingInvoiceDetail({ id }: { id: string }) {
  const query = useBillingInvoice(id);
  if (query.isLoading) return <p role="status"><Loader2 className="mr-2 inline h-4 w-4 animate-spin" aria-hidden /> Loading invoice…</p>;
  if (query.isError) return <ErrorState title="This invoice did not load" message={formatBillingError(query.error)} onRetry={() => void query.refetch()} actionHref="/billing/invoices" actionLabel="Recent invoices" />;
  if (!query.data) return <Card><CardContent className="py-10 text-center"><h2 className="font-semibold">Invoice not found</h2><p className="mt-2 text-sm text-muted-foreground">This invoice is unavailable for this account.</p><Button asChild className="mt-4"><Link href="/billing/invoices">Recent invoices</Link></Button></CardContent></Card>;
  return <InvoiceFacts invoice={query.data} />;
}

export function InvoiceFacts({ invoice }: { invoice: BillingInvoice }) {
  const money = (value: number | null, currency = invoice.currency) => formatMinorMoney(value, currency,
    currency?.toLowerCase() === invoice.currency?.toLowerCase() ? invoice.currency_exponent : null);
  const reference = invoice.invoice_number ?? invoice.stripe_invoice_id ?? invoice.id;
  return <div className="space-y-6">
    <div className="flex flex-wrap gap-3"><Button asChild variant="outline" size="sm"><Link href="/billing/invoices"><ArrowLeft className="mr-1 h-4 w-4" aria-hidden /> Recent invoices</Link></Button>
      <Button variant="outline" size="sm" onClick={() => window.print()}><Printer className="mr-1 h-4 w-4" aria-hidden /> Print</Button><Documents invoice={invoice} /></div>
    <Card><CardHeader><div className="flex items-center justify-between gap-3"><CardTitle>{reference}</CardTitle><InvoiceStatus status={invoice.status} /></div>
      <CardDescription>{invoice.detail_source === "provider_snapshot" ? "Captured provider invoice" : "Stored invoice summary"}{invoice.captured_at ? ` · captured ${new Date(invoice.captured_at).toLocaleString()}` : ""}</CardDescription>
    </CardHeader><CardContent className="space-y-4">
      {invoice.detail_status !== "complete" && <p role="status" className="rounded-lg border p-3 text-sm">{invoice.detail_status === "partial" ? "Some invoice details are unavailable." : "Detailed invoice facts were not captured."} Missing values are not zero. Use the provider invoice or PDF where available.</p>}
      <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        {[["Invoice period", `${invoiceDate(invoice.period_start)} – ${invoiceDate(invoice.period_end)}`], ["Due date", invoiceDate(invoice.due_date)], ["Paid date", invoiceDate(invoice.paid_at)], ["Recorded locally", invoiceDate(invoice.created_at)],
          ["Subtotal", money(invoice.subtotal)], ["Invoice total", money(invoice.total)], ["Provider amount due", money(invoice.amount_due)], ["Amount paid", money(invoice.amount_paid)], ["Amount remaining", money(invoice.amount_remaining)]].map(([label, value]) => <div key={label}><dt className="text-xs text-muted-foreground">{label}</dt><dd className="mt-1 font-semibold tabular-nums">{value}</dd></div>)}
      </dl>
    </CardContent></Card>
    <Card><CardHeader><CardTitle>Invoice line items</CardTitle><CardDescription>Recorded invoice amounts. Call usage and allowance are shown separately on Billing.</CardDescription></CardHeader><CardContent>
      {invoice.line_items === null ? <p>Line items unavailable.</p> : invoice.line_items.length === 0 ? <p>No line items recorded.</p> :
        <div className="overflow-x-auto"><table className="min-w-full text-sm"><caption className="sr-only">Captured provider invoice line items</caption><thead><tr className="border-b text-left"><th scope="col" className="p-3">Description</th><th scope="col" className="p-3">Quantity</th><th scope="col" className="p-3">Period</th><th scope="col" className="p-3">Amount</th></tr></thead><tbody>
          {invoice.line_items.map((line) => <tr key={line.id} className="border-b"><td className="p-3">{line.description ?? "Description unavailable"}</td><td className="p-3">{line.quantity?.toLocaleString() ?? "Unavailable"}</td><td className="p-3">{invoiceDate(line.period_start)} – {invoiceDate(line.period_end)}</td><td className="p-3 tabular-nums">{money(line.amount, line.currency)}</td></tr>)}
        </tbody></table></div>}
    </CardContent></Card>
    <Card><CardHeader><CardTitle>Taxes, discounts and credit notes</CardTitle></CardHeader><CardContent className="space-y-4">
      <section aria-label="Invoice taxes"><h3 className="font-semibold">Taxes</h3>{invoice.taxes === null ? <p>Tax details unavailable.</p> : invoice.taxes.length === 0 ? <p>No taxes recorded.</p> : <ul>{invoice.taxes.map((tax, i) => <li key={tax.tax_rate_id ?? i}>{money(tax.amount)}{tax.inclusive === true ? " (included in the recorded price)" : tax.inclusive === false ? " (exclusive)" : ""}</li>)}</ul>}</section>
      <section aria-label="Invoice discounts"><h3 className="font-semibold">Discounts</h3>{invoice.discounts === null ? <p>Discount details unavailable.</p> : invoice.discounts.length === 0 ? <p>No discounts recorded.</p> : <ul>{invoice.discounts.map((discount, i) => <li key={discount.discount_id ?? i}>{money(discount.amount)}</li>)}</ul>}</section>
      <section aria-label="Invoice credit notes"><h3 className="font-semibold">Credit notes</h3>{invoice.credits === null ? <p>Credit-note details unavailable.</p> : invoice.credits.length === 0 ? <p>No credit notes recorded.</p> : <ul className="space-y-2">{invoice.credits.map((credit) => <li key={credit.id} className="rounded-lg border p-3"><p>{credit.id} · {money(credit.amount, credit.currency)} · {credit.status ?? "Status unavailable"}</p>{credit.pdf && <a className="underline" href={credit.pdf} target="_blank" rel="noopener noreferrer">Credit note PDF</a>}{credit.refunds && credit.refunds.length > 0 && <p className="text-sm text-muted-foreground">Linked refund references: {credit.refunds.map((refund) => refund.id).join(", ")}. References do not confirm bank receipt.</p>}</li>)}</ul>}</section>
      <ProviderRefundFacts refunds={invoice.refunds?.map((refund) => ({ ...refund, currency_exponent: refund.currency?.toLowerCase() === invoice.currency?.toLowerCase() ? invoice.currency_exponent : null })) ?? null} detailStatus={invoice.detail_status} capturedAt={invoice.captured_at} />
    </CardContent></Card>
    <BillingSupport reference={reference} />
  </div>;
}
