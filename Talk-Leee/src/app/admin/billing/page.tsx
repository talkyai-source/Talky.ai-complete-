"use client";

import Link from "next/link";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { FeatureUnavailable } from "@/components/admin/feature-unavailable";

export default function AdminBillingPage() {
  return (
    <DashboardLayout title="Partner Billing" description="Cross-partner billing rollup — not available from this console.">
      <div className="space-y-6">
        <FeatureUnavailable
          title="Partner billing"
          purpose="Revenue, minutes, tenant counts and overage charges aggregated across every white-label partner."
          reason="There is no cross-partner rollup in the API. The mounted billing router (/api/v1/billing) serves only the signed-in tenant's own subscription, usage and invoices; it has no /billing/partners route, so the totals this page used to show were not aggregates of anything."
        />
        <p className="text-sm text-muted-foreground">
          Your own account&apos;s plan, usage and invoices are real and live at{" "}
          <Link href="/billing" className="font-medium text-foreground underline underline-offset-2">
            Billing
          </Link>
          .
        </p>
      </div>
    </DashboardLayout>
  );
}
