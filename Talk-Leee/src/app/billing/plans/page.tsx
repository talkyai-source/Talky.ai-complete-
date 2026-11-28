"use client";
import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { BillingPlanSelector, parseBillingInterval } from "@/components/billing/billing-plan-selector";
import { useAuth } from "@/lib/auth-context";
// The pricing page's plan buttons open this page with `?interval=month` or `?interval=year`.
function PlanSelectorForAddress({ scope }: { scope: string }) {
  const searchParams = useSearchParams();
  return <BillingPlanSelector key={scope} scope={scope} initialInterval={parseBillingInterval(searchParams?.get("interval")) ?? undefined} />;
}
export default function PlansPage() {
  const { user, loading } = useAuth();
  const scope = user?.tenant_id && user.id ? `${user.tenant_id}:${user.id}` : null;
  return <DashboardLayout title="Plans" description="Choose an available billing offer.">
    {loading ? <p role="status">Loading your account…</p> : scope ? <Suspense fallback={<p role="status">Loading your account…</p>}><PlanSelectorForAddress scope={scope} /></Suspense> : <p role="alert">Your billing account could not be verified. Reload or sign in again before choosing a plan.</p>}
  </DashboardLayout>;
}
