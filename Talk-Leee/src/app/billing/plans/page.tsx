"use client";

import { useState } from "react";
import Link from "next/link";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ArrowLeft, Check, Loader2, Star } from "lucide-react";
import { useBillingPlan, useBillingPlans } from "@/lib/billing-api";
import { api } from "@/lib/api";

type PlanRow = {
  id: string;
  name: string;
  price: number;
  description?: string;
  minutes: number;
  agents: number;
  concurrent_calls: number;
  features: string[];
  not_included?: string[];
  popular?: boolean;
  billing_period?: string;
};

function formatCurrency(amount: number) {
  return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", minimumFractionDigits: 0 }).format(amount);
}

export default function PlansPage() {
  const [isYearly, setIsYearly] = useState(false);
  const [checkoutPlanId, setCheckoutPlanId] = useState<string | null>(null);
  const [checkoutError, setCheckoutError] = useState<string | null>(null);
  const plansQuery = useBillingPlans();
  const subQuery = useBillingPlan();

  const plans = (plansQuery.data ?? []) as PlanRow[];
  const subscription = subQuery.data as { plan_id?: string | null } | null;
  const currentPlanId = subscription?.plan_id ?? "";

  const loading = plansQuery.isLoading || subQuery.isLoading;

  async function startCheckout(planId: string) {
    setCheckoutError(null);
    setCheckoutPlanId(planId);
    try {
      const origin = typeof window !== "undefined" ? window.location.origin : "";
      const data = await api.request<{ checkout_url?: string; mock_mode?: boolean; message?: string }>({
        path: "/billing/create-checkout-session",
        method: "POST",
        body: {
          plan_id: planId,
          success_url: `${origin}/billing?checkout=success`,
          cancel_url: `${origin}/billing/plans?checkout=cancel`,
        },
      });
      if (data.mock_mode) {
        setCheckoutError(
          "Stripe is not configured on the server yet — clicking a plan would normally redirect to Stripe Checkout. " +
          "Add STRIPE_SECRET_KEY + STRIPE_PUBLISHABLE_KEY + STRIPE_WEBHOOK_SECRET to backend/.env and set each plan's stripe_price_id."
        );
        setCheckoutPlanId(null);
        return;
      }
      if (!data.checkout_url) throw new Error("No checkout URL returned");
      window.location.href = data.checkout_url;
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Failed to start checkout";
      setCheckoutError(msg);
      setCheckoutPlanId(null);
    }
  }

  return (
    <DashboardLayout title="Plans" description="Choose the plan that fits your needs.">
      <div className="space-y-6">
        <div className="flex items-center gap-3">
          <Button asChild variant="outline" size="sm">
            <Link href="/billing"><ArrowLeft className="mr-1 h-4 w-4" aria-hidden /> Back to Billing</Link>
          </Button>
        </div>

        {checkoutError && (
          <div role="alert" className="rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm font-medium text-red-700 dark:text-red-400">
            {checkoutError}
          </div>
        )}

        <div className="flex items-center justify-center gap-3">
          <span className={`text-sm font-semibold ${!isYearly ? "text-foreground" : "text-muted-foreground"}`}>Monthly</span>
          <button
            type="button"
            role="switch"
            aria-checked={isYearly}
            onClick={() => setIsYearly(!isYearly)}
            className={`relative inline-flex h-7 w-12 items-center rounded-full transition-colors ${isYearly ? "bg-emerald-500" : "bg-muted"}`}
          >
            <span className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ${isYearly ? "translate-x-6" : "translate-x-1"}`} />
          </button>
          <span className={`text-sm font-semibold ${isYearly ? "text-foreground" : "text-muted-foreground"}`}>
            Yearly <span className="text-emerald-600 dark:text-emerald-400 text-xs font-bold ml-1">Save ~17%</span>
          </span>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-16 text-muted-foreground">
            <Loader2 className="mr-2 h-5 w-5 animate-spin" aria-hidden /> Loading plans…
          </div>
        ) : plans.length === 0 ? (
          <Card>
            <CardContent className="py-10 text-center text-sm text-muted-foreground">
              No plans available yet.
            </CardContent>
          </Card>
        ) : (
          <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-4">
            {plans.map((plan, idx) => {
              const isCurrent = plan.id === currentPlanId;
              const monthly = plan.price;
              const yearly = Math.round(plan.price * 12 * 0.83);
              const price = isYearly ? yearly : monthly;
              const currentIdx = plans.findIndex((p) => p.id === currentPlanId);

              return (
                <Card key={plan.id} className={`relative flex flex-col ${plan.popular ? "ring-2 ring-emerald-500 dark:ring-emerald-400" : ""} ${isCurrent ? "border-primary" : ""}`}>
                  {plan.popular && (
                    <div className="absolute -top-3 left-1/2 -translate-x-1/2">
                      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500 px-3 py-1 text-xs font-bold text-white">
                        <Star className="h-3 w-3" aria-hidden /> Recommended
                      </span>
                    </div>
                  )}
                  <CardHeader className="text-center">
                    <CardTitle className="text-xl">{plan.name}</CardTitle>
                    <CardDescription>
                      <span className="text-3xl font-black text-foreground tabular-nums">{formatCurrency(price)}</span>
                      <span className="text-sm text-muted-foreground">/{isYearly ? "year" : "month"}</span>
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="flex flex-1 flex-col">
                    <ul className="flex-1 space-y-2">
                      {plan.features.map((f) => (
                        <li key={f} className="flex items-start gap-2 text-sm text-muted-foreground">
                          <Check className="mt-0.5 h-4 w-4 flex-shrink-0 text-emerald-500" aria-hidden />
                          <span>{f}</span>
                        </li>
                      ))}
                    </ul>
                    <div className="mt-4 space-y-2 text-xs text-muted-foreground">
                      <div>Minutes included: {plan.minutes.toLocaleString()}</div>
                      <div>Concurrent calls: {plan.concurrent_calls}</div>
                    </div>
                    <div className="mt-6">
                      {isCurrent ? (
                        <Button disabled className="w-full" variant="outline">Current Plan</Button>
                      ) : (
                        <Button
                          className="w-full"
                          variant={plan.popular ? "default" : "outline"}
                          disabled={checkoutPlanId !== null}
                          onClick={() => startCheckout(plan.id)}
                        >
                          {checkoutPlanId === plan.id ? (
                            <><Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> Redirecting…</>
                          ) : (
                            currentIdx === -1 || idx > currentIdx ? "Upgrade" : "Downgrade"
                          )}
                        </Button>
                      )}
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}
      </div>
    </DashboardLayout>
  );
}
