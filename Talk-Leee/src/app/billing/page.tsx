"use client";

import { Suspense } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { BillingOverview } from "@/components/billing/billing-overview";
import { TopupCard } from "@/components/billing/topup-card";
import { BillingCheckoutReturn } from "@/components/billing/billing-plan-selector";
import { useAuth } from "@/lib/auth-context";

export default function BillingPage() {
  const { user } = useAuth();
  const scope = user?.tenant_id && user.id ? `${user.tenant_id}:${user.id}` : undefined;
  return (
    <DashboardLayout title="Billing & usage" description="Manage your plan, minutes, top-ups, invoices, and account activity.">
      {scope && <BillingCheckoutReturn key={scope} scope={scope} />}
      <BillingOverview
        scope={scope}
        // Suspense: TopupCard reads the ?topup= return param via
        // useSearchParams, which Next requires a boundary for. It is passed in
        // as a slot so BillingOverview itself stays free of the app-router
        // hooks and can be rendered in a test.
        topupSlot={
          <Suspense fallback={null}>
            <TopupCard />
          </Suspense>
        }
      />
    </DashboardLayout>
  );
}
