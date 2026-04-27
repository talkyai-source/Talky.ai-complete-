"use client";

import { useState } from "react";
import Link from "next/link";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ArrowLeft, Check, Star } from "lucide-react";
import { PLANS, CURRENT_TENANT_PLAN } from "@/lib/billing-mock-data";

function formatCurrency(amount: number) {
  return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", minimumFractionDigits: 0 }).format(amount);
}

export default function PlansPage() {
  const [isYearly, setIsYearly] = useState(false);
  const currentPlanId = CURRENT_TENANT_PLAN.planId;

  return (
    <DashboardLayout title="Plans" description="Choose the plan that fits your needs.">
      <div className="space-y-6">
        <div className="flex items-center gap-3">
          <Button asChild variant="outline" size="sm">
            <Link href="/billing"><ArrowLeft className="mr-1 h-4 w-4" aria-hidden /> Back to Billing</Link>
          </Button>
        </div>

        {/* Billing Toggle */}
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

        {/* Plan Cards */}
        <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-4">
          {PLANS.map((plan) => {
            const isCurrent = plan.id === currentPlanId;
            const price = isYearly ? plan.priceYearly : plan.priceMonthly;

            return (
              <Card key={plan.id} className={`relative flex flex-col ${plan.recommended ? "ring-2 ring-emerald-500 dark:ring-emerald-400" : ""} ${isCurrent ? "border-primary" : ""}`}>
                {plan.recommended && (
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
                    <div>Overage: {formatCurrency(plan.overagePerMinute)}/min</div>
                    <div>Extra concurrency: {formatCurrency(plan.overageConcurrencyPerSlot)}/slot</div>
                  </div>
                  <div className="mt-6">
                    {isCurrent ? (
                      <Button disabled className="w-full" variant="outline">Current Plan</Button>
                    ) : (
                      <Button className="w-full" variant={plan.recommended ? "default" : "outline"}>
                        {PLANS.indexOf(plan) > PLANS.findIndex((p) => p.id === currentPlanId) ? "Upgrade" : "Downgrade"}
                      </Button>
                    )}
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      </div>
    </DashboardLayout>
  );
}
