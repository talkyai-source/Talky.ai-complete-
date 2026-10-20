import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PLAN_DISPLAY_CATALOG } from "@/lib/plan-display-catalog";

/** Published plan details are not payment offers or account entitlements. */
export function PublishedPlanCards() {
  return <section aria-label="Subscription plans" className="space-y-4">
    <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
      {PLAN_DISPLAY_CATALOG.map(plan => <Card key={plan.id} className="flex flex-col">
        <CardHeader>
          <CardTitle>{plan.name}</CardTitle>
          <p className="text-3xl font-semibold tracking-tight">{plan.monthlyPrice}</p>
          <CardDescription>Annual billing equivalent: {plan.annualEquivalent}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-1 flex-col gap-5">
          <ul className="flex-1 space-y-2 text-sm">
            {plan.highlights.map(feature => <li key={feature}>{feature}</li>)}
          </ul>
          <p className="text-sm text-muted-foreground">Online checkout is not available for this plan yet.</p>
          <Button disabled aria-label={`${plan.name} checkout unavailable`}>Checkout unavailable</Button>
        </CardContent>
      </Card>)}
    </div>
    <p className="text-sm text-muted-foreground">
      Telephone/PSTN/SIP usage and premium voices are billed separately. Annual figures are monthly equivalents;
      annual checkout is not available yet.
    </p>
    <div className="flex flex-wrap gap-3">
      <Button asChild variant="outline"><Link href="/pricing">Compare all plan features</Link></Button>
      <Button asChild variant="outline"><Link href="/contact">Contact sales</Link></Button>
    </div>
  </section>;
}
