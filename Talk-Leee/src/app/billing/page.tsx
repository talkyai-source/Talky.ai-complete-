"use client";

import { Suspense } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { BillingOverview } from "@/components/billing/billing-overview";
import { TopupCard } from "@/components/billing/topup-card";

export default function BillingPage() {
  return (
    <DashboardLayout title="Billing" description="Manage your subscription, track usage, and view invoices.">
      <BillingOverview
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
