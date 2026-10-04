"use client";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { BillingPlanSelector } from "@/components/billing/billing-plan-selector";
import { useAuth } from "@/lib/auth-context";
export default function PlansPage() {
  const { user, loading } = useAuth();
  const scope = user?.tenant_id && user.id ? `${user.tenant_id}:${user.id}` : null;
  return <DashboardLayout title="Plans" description="Choose an available billing offer.">
    {loading ? <p role="status">Loading your account…</p> : scope ? <BillingPlanSelector key={scope} scope={scope} /> : <p role="alert">Your billing account could not be verified. Reload or sign in again before choosing a plan.</p>}
  </DashboardLayout>;
}
