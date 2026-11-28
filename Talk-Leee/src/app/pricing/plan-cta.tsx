"use client";

import Link from "next/link";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth-context";
import { useBillingInterval } from "./billing-period";

// Where a plan or minutes purchase starts. These are the dashboard's own pages;
// this component starts no checkout of its own.
//   plans:   src/app/billing/plans/page.tsx (BillingPlanSelector, "Select <plan>")
//   minutes: src/app/billing/page.tsx (TopupCard, titled "Pay as you go" for an account with no plan)
export const PLAN_PAGE = "/billing/plans/";
export const MINUTES_PAGE = "/billing/";

export function planPageFor(interval: "month" | "year"): string {
  return `${PLAN_PAGE}?interval=${interval}`;
}

/** The sign-up page, carrying where the visitor was going. The sign-up page checks the value before following it. */
export function signUpFor(next: string): string {
  return `/auth/register/?next=${encodeURIComponent(next)}`;
}

/**
 * One button of the pricing page. Signed in, it opens the dashboard page where
 * the purchase starts; signed out, it opens sign-up and carries that page as
 * `next`. A plan button names its plan the way the dashboard does ("Select
 * Basic") and carries the billing period chosen above it.
 */
export function PlanCta({ target, label, className }: { target: "plan" | "minutes"; label: string; className: string }) {
  const { status } = useAuth();
  const interval = useBillingInterval();
  const destination = target === "plan" ? planPageFor(interval) : MINUTES_PAGE;
  const href = status === "authenticated" ? destination : signUpFor(destination);
  return (
    <Button asChild size="lg" className={className}>
      <Link href={href}>{label}</Link>
    </Button>
  );
}
