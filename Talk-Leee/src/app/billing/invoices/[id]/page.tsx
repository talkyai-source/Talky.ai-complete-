import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { BillingInvoiceDetail } from "@/components/billing/invoice-view";

export default async function InvoiceDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <DashboardLayout title="Invoice" description="Recorded invoice facts and provider documents."><BillingInvoiceDetail id={id} /></DashboardLayout>;
}
