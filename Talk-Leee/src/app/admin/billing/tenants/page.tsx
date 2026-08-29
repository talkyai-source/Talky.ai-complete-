"use client";

import Link from "next/link";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { ArrowLeft } from "lucide-react";
import { FeatureUnavailable } from "@/components/admin/feature-unavailable";

export default function TenantBillingPage() {
  return (
    <DashboardLayout title="Tenant Billing" description="Per-tenant billing breakdown — not available from this console.">
      <div className="space-y-6">
        <div className="flex items-center gap-3">
          <Button asChild variant="outline" size="sm">
            <Link href="/admin/billing"><ArrowLeft className="mr-1 h-4 w-4" aria-hidden /> Partner Overview</Link>
          </Button>
        </div>

        <FeatureUnavailable
          title="Tenant billing"
          purpose="Plan, billing state, minutes and payment status for every tenant, side by side."
          reason="There is no cross-tenant billing endpoint in the API. The mounted billing router (/api/v1/billing) is scoped to the signed-in tenant and has no /billing/tenants route, so this table could never have been populated from real accounts."
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
