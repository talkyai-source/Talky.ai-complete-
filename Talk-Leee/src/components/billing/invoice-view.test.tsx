import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { cleanup, screen, within } from "@testing-library/react";
import type { QueryClient } from "@tanstack/react-query";
import { BillingInvoiceDetail, BillingInvoiceList } from "@/components/billing/invoice-view";
import { renderWithQueryClient } from "@/test-utils/render";
import { invoiceFixture } from "@/test-utils/billing-fixtures";
import { invoiceSchema, invoiceListSchema, formatMinorMoney } from "@/lib/billing-read";

const originalFetch = globalThis.fetch;
const clients: QueryClient[] = [];
function show(ui: React.ReactElement) { const result = renderWithQueryClient(ui); clients.push(result.qc); return result; }
function respond(data: unknown, status = 200) { globalThis.fetch = async () => new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } }); }
afterEach(() => { cleanup(); globalThis.fetch = originalFetch; clients.splice(0).forEach((client) => client.clear()); });

test("paid detail separates original total and zero remaining, retains captured lines and provider documents", async () => {
  respond({ ...invoiceFixture(), planName: "Changed current plan", includedMinutes: 99999 });
  show(<BillingInvoiceDetail id={invoiceFixture().id} />);
  await screen.findByText("Original purchased service");
  assert.equal(within(screen.getByText("Amount remaining").parentElement!).getByText("£0.00").textContent, "£0.00");
  assert.equal(within(screen.getByText("Invoice total").parentElement!).getByText("£30.00").textContent, "£30.00");
  assert.equal(screen.queryByText("Total Due"), null);
  assert.equal(screen.queryByText(/Changed current plan|Included Minutes|Usage Breakdown/), null);
  assert.equal(screen.getByRole("link", { name: "Download PDF" }).getAttribute("href"), invoiceFixture().invoice_pdf);
  assert.equal(screen.getByRole("link", { name: "Download PDF" }).querySelector("button"), null);
});

test("partial detail preserves known totals but never fills absent tax, lines or credits with zero", async () => {
  respond(invoiceFixture({ detail_status: "partial", subtotal: null, taxes: null, discounts: null, credits: null, refunds: null, line_items: null }));
  show(<BillingInvoiceDetail id={invoiceFixture().id} />);
  await screen.findByText(/Some invoice details are unavailable/);
  assert.ok(screen.getByText("Tax details unavailable."));
  assert.ok(screen.getByText("Line items unavailable."));
  assert.ok(screen.getByText("Credit-note details unavailable."));
  assert.equal(within(screen.getByText("Subtotal").parentElement!).getByText("Unavailable").textContent, "Unavailable");
});

test("recorded zero line and credit/refund facts are visible without claiming bank receipt", async () => {
  const base = invoiceFixture();
  respond(invoiceFixture({ line_items: [{ ...base.line_items![0], amount: 0, quantity: 0 }],
    credits: [{ id: "cn_synthetic", amount: 1000, currency: "gbp", status: "issued", type: "post_payment", pre_payment_amount: 0, post_payment_amount: 1000, pdf: null, refunds: [{ id: "re_synthetic", amount: 1000 }] }],
    refunds: [{ id: "re_synthetic", amount: 1000, currency: "gbp", status: "pending", charge_id: "ch_synthetic", payment_intent_id: "pi_synthetic", created_at: null }] }));
  show(<BillingInvoiceDetail id={base.id} />);
  const line = await screen.findByText("Original purchased service");
  assert.ok(within(line.closest("tr")!).getByText("£0.00"));
  assert.equal(screen.queryByText("Included"), null);
  assert.ok(screen.getByText(/Provider status: pending/));
  assert.ok(screen.getByText(/Provider status does not confirm when funds reach a bank/));
  assert.match(screen.getByRole("link", { name: /contact billing@/ }).getAttribute("href")!, /^mailto:billing@talkleeai.com/);
});

test("recent invoice list formats JPY and KWD from their actual minor-unit precision", async () => {
  respond({ invoices: [invoiceFixture({ currency: "jpy", currency_exponent: 0, total: 1000 }), invoiceFixture({ id: "00000000-0000-4000-8000-000000000402", stripe_invoice_id: "in_second", invoice_number: "SECOND", currency: "kwd", currency_exponent: 3, total: 1234 })], count: 2 });
  show(<BillingInvoiceList />);
  await screen.findByText("¥1,000");
  assert.ok(screen.getByText(/KWD\s1\.234/));
  assert.ok(screen.getByText(/Up to 10 most recent/));
  assert.equal(screen.queryByText(/Complete invoice history/), null);
});

for (const [name, body, status] of [
  ["forbidden", { detail: "Forbidden" }, 403], ["outage", { detail: "Unavailable" }, 503],
  ["malformed success", { id: "wrong" }, 200], ["empty success", null, 200],
  ["unsafe document URL", invoiceFixture({ invoice_pdf: "javascript:alert(1)" }), 200],
] as const) test(`${name} stays a load failure, not a missing or zero-valued invoice`, async () => {
  respond(body, status); show(<BillingInvoiceDetail id={invoiceFixture().id} />);
  await screen.findByText("This invoice did not load");
  assert.equal(screen.queryByText("Invoice not found"), null);
  assert.equal(screen.queryByText("£0.00"), null);
});

test("only a server 404 renders invoice not found", async () => {
  respond({ detail: "Not found" }, 404); show(<BillingInvoiceDetail id={invoiceFixture().id} />);
  await screen.findByText("Invoice not found");
  assert.equal(screen.queryByText("This invoice did not load"), null);
});

test("actual synthetic-provider PostgreSQL API capture renders its recorded historical facts", async () => {
  const evidence = JSON.parse(readFileSync(new URL("../../../../docs/sessions/artifacts/cp04/reconciliation-example.json", import.meta.url), "utf8"));
  const invoice = invoiceSchema.parse(evidence.api_invoice);
  assert.deepEqual(invoiceListSchema.parse(evidence.api_list), [invoice]);
  respond(evidence.api_invoice);
  show(<BillingInvoiceDetail id={invoice.id} />);
  await screen.findByText("Original purchased annual terms");
  for (const [label, amount] of [["Invoice total", invoice.total], ["Amount remaining", invoice.amount_remaining], ["Provider amount due", invoice.amount_due]] as const) {
    assert.ok(within(screen.getByText(label).parentElement!).getByText(formatMinorMoney(amount, invoice.currency, invoice.currency_exponent)));
  }
  assert.equal(screen.queryByText(/Included Minutes|Usage Breakdown|Current plan/), null);
});
