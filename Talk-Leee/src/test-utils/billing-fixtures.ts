import type { BillingInvoice } from "@/lib/billing-read";

export function invoiceFixture(change: Partial<BillingInvoice> = {}): BillingInvoice {
  return { id: "00000000-0000-4000-8000-000000000401", stripe_invoice_id: "in_synthetic_cp04", invoice_number: "INV-SYNTHETIC",
    status: "paid", currency: "gbp", currency_exponent: 2, subtotal: 2500, total: 3000, amount_due: 3000, amount_paid: 3000, amount_remaining: 0,
    period_start: "2026-09-01T00:00:00Z", period_end: "2026-10-01T00:00:00Z", paid_at: "2026-09-01T00:00:01Z", due_date: null, created_at: "2026-09-01T00:00:02Z",
    invoice_pdf: "https://invoice.stripe.com/i/synthetic/pdf", hosted_invoice_url: "https://invoice.stripe.com/i/synthetic",
    detail_status: "complete", detail_source: "provider_snapshot", captured_at: "2026-09-01T00:00:03Z", source_reference: "evt_synthetic_cp04",
    line_items: [{ id: "il_original", description: "Original purchased service", quantity: 1, amount: 2500, currency: "gbp", period_start: "2026-09-01T00:00:00Z", period_end: "2026-10-01T00:00:00Z" }],
    taxes: [{ amount: 500, inclusive: false, tax_rate_id: "txr_synthetic" }], discounts: [], credits: [], refunds: [], ...change };
}
