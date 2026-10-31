import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { BillingInvoiceList } from "@/components/billing/invoice-view";

export default function InvoicesPage() {
  return <DashboardLayout title="Invoices" description="View your recent saved invoices and provider documents."><BillingInvoiceList /></DashboardLayout>;
}
