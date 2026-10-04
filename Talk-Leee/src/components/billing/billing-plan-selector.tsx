"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useBillingPlan, useBillingPlans } from "@/lib/billing-api";
import { formatPurchasedPrice, hasManagedSubscription, openBillingPortal } from "@/lib/billing-purchase";
import { purchaseErrorText, useBillingPurchase } from "./use-billing-purchase";

const navigate = (url: string) => window.location.assign(url);
function PurchaseNotice({ purchase }: { purchase: ReturnType<typeof useBillingPurchase> }) {
  const { saved, attempt, error, busy, storageAvailable, returned } = purchase;
  return <div className="space-y-3">
    {error && <p role="alert" className="rounded-xl border border-red-500/30 p-4 text-sm text-red-700 dark:text-red-400">{error}</p>}
    {!storageAvailable && <p role="alert" className="text-sm">The browser could not read or update the saved purchase. Enable session storage and reload before starting another purchase.</p>}
    {(returned === "cancel" || returned === "cancelled") && <p role="status" className="text-sm">You returned from checkout. This does not confirm whether payment completed. Check or resume the saved purchase below.</p>}
    {returned === "success" && attempt?.state !== "activated" && <p role="status" className="text-sm">Payment confirmation is pending. Returning from checkout does not activate a subscription.</p>}
    {attempt?.state === "activated" && <p role="status" className="rounded-xl border border-emerald-500/30 p-4">The server confirmed this purchase. Your current plan is shown from billing records.</p>}
    {(attempt?.state === "expired" || attempt?.state === "failed") && <p role="status" className="text-sm">The server confirmed the saved checkout {attempt.state === "expired" ? "expired" : "failed"}. You may choose an available offer again.</p>}
    {saved && <div className="space-y-3 rounded-xl border p-4">
      <p className="font-medium">Saved purchase: {saved.price_option.plan_name} — {formatPurchasedPrice(saved.price_option)}</p>
      <p className="text-sm">{attempt?.state === "open" ? "Checkout is open. Resume this purchase instead of creating another." : "This purchase is not yet confirmed. Its selection is kept for safe retry."}</p>
      <p className="break-all text-xs text-muted-foreground">Request reference: {saved.request_id}</p>
      <div className="flex flex-wrap gap-2"><Button disabled={busy} onClick={() => void purchase.run()}>{busy ? "Checking…" : "Resume saved purchase"}</Button><Button variant="outline" disabled={busy} onClick={() => void purchase.check()}>Check status</Button></div>
    </div>}
  </div>;
}
export function BillingCheckoutReturn({ scope, onNavigate = navigate }: { scope: string; onNavigate?: (url: string) => void }) {
  const purchase = useBillingPurchase(scope, onNavigate);
  return <PurchaseNotice purchase={purchase} />;
}
export function BillingPlanSelector({ scope, onNavigate = navigate }: { scope: string; onNavigate?: (url: string) => void }) {
  const plansQuery = useBillingPlans(scope);
  const subscriptionQuery = useBillingPlan(scope);
  const purchase = useBillingPurchase(scope, onNavigate);
  const [interval, setInterval] = useState<"month" | "year">("month");
  const [portalError, setPortalError] = useState("");
  const [portalBusy, setPortalBusy] = useState(false);
  const portalLock = useRef(false);
  const alive = useRef(true);
  const portalController = useRef<AbortController | null>(null);
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; portalController.current?.abort(); };
  }, []);
  const plans = plansQuery.data ?? [];
  const subscription = subscriptionQuery.data ?? null;
  const managed = hasManagedSubscription(subscription);
  const annualAvailable = plans.some(plan => ["live", "test"].includes(plan.billing_mode) && plan.price_options.some(price => price.interval === "year" && price.kind === "stripe" && price.active && price.checkout_available));
  const selectedInterval = annualAvailable ? interval : "month";
  const blocked = !purchase.ready || purchase.busy || Boolean(purchase.saved) || !purchase.storageAvailable || purchase.attempt?.state === "activated" || managed;
  async function manage() {
    if (portalLock.current) return;
    portalLock.current = true; setPortalBusy(true); setPortalError("");
    const controller = new AbortController(); portalController.current = controller;
    try { const url = await openBillingPortal(controller.signal); if (alive.current) onNavigate(url); }
    catch (failure) { if (alive.current) setPortalError(purchaseErrorText(failure)); }
    finally { portalLock.current = false; if (alive.current) setPortalBusy(false); }
  }
  return <div className="space-y-6">
    <PurchaseNotice purchase={purchase} />
    {portalError && <p role="alert">{portalError}</p>}
    {plansQuery.isLoading || subscriptionQuery.isLoading ? <p role="status">Loading billing offers…</p> : plansQuery.isError || subscriptionQuery.isError ?
      <div role="alert" className="space-y-3"><p>Billing offers could not be verified. Your current subscription is unchanged.</p><Button variant="outline" onClick={() => { void plansQuery.refetch(); void subscriptionQuery.refetch(); }}>Retry</Button></div> : <>
      {managed && <div className="space-y-3 rounded-xl border p-4"><p>Manage your existing paid subscription in the billing portal.</p>{subscription?.billing_portal_available ? <Button disabled={portalBusy} onClick={() => void manage()}>{portalBusy ? "Opening…" : "Manage subscription"}</Button> : <p>Subscription management is unavailable. Contact support before changing your plan.</p>}</div>}
      {annualAvailable && <fieldset className="flex flex-wrap items-center justify-center gap-4"><legend className="mb-2 text-center font-semibold">Billing interval</legend>{(["month", "year"] as const).map(value => <label key={value} className="inline-flex items-center gap-2"><input type="radio" name="billing-interval" value={value} checked={selectedInterval === value} disabled={Boolean(purchase.saved) || purchase.busy} onChange={() => setInterval(value)} />{value === "month" ? "Monthly" : "Yearly"}</label>)}</fieldset>}
      {plans.length === 0 && <p>No approved offers are currently available.</p>}
      <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-4">{plans.map(plan => {
        const price = plan.price_options.find(option => option.kind === "free" && option.active) ?? plan.price_options.find(option => option.interval === selectedInterval && option.active);
        const available = Boolean(price?.checkout_available && price.active && (price.kind === "free" || ["live", "test"].includes(plan.billing_mode)));
        const currentFree = price?.kind === "free" && subscription?.purchased_price_option?.id === price.id;
        return <Card key={plan.id} className="flex flex-col"><CardHeader><CardTitle>{plan.name}</CardTitle><CardDescription>{price ? formatPurchasedPrice(price) : "No approved offer for this interval"}</CardDescription>{plan.billing_mode === "test" && price?.kind === "stripe" && <p className="text-sm">Test billing — not a live purchase</p>}</CardHeader><CardContent className="flex flex-1 flex-col gap-4">
          <ul className="flex-1 space-y-2 text-sm">{plan.features.map(feature => <li key={feature}>{feature}</li>)}</ul>
          <p className="text-sm">Monthly included allowance: {plan.minutes === 0 ? "Unlimited" : `${plan.minutes.toLocaleString()} minutes`}<br />Concurrent calls: {plan.concurrent_calls}</p>
          {!available && <p className="text-sm">{price?.unavailable_reason || "This offer is not available for checkout."}</p>}
          <Button disabled={blocked || !available || currentFree} onClick={() => { if (price) void purchase.run({ plan, price }); }}>{currentFree ? "Current free plan" : price?.kind === "free" ? `Choose ${plan.name}` : `Select ${plan.name}`}</Button>
        </CardContent></Card>;
      })}</div>
    </>}
    <Button asChild variant="outline"><Link href="/billing">Back to billing</Link></Button>
  </div>;
}
