"use client";
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import type { BillingSubscription } from "@/lib/billing-purchase";
import { cancelPaypalPlan, formatPlanDate } from "@/lib/paypal-plan-api";

/** The "manage" box for a plan paid with PayPal: no Stripe portal, only cancelling (PayPal stops charging at once; the plan runs to the end of the paid period). */
export function PaypalPlanManage({ subscription }: { subscription: BillingSubscription }) {
  const qc = useQueryClient();
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const alive = useRef(true);
  const lock = useRef(false);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; controller.current?.abort(); };
  }, []);
  const endDate = formatPlanDate(subscription.current_period_end);
  // Only an active plan has paid time left; one whose payment failed ends when it is cancelled.
  const paidUp = subscription.status === "active";
  async function cancel() {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError("");
    const abort = new AbortController(); controller.current = abort;
    try {
      const result = await cancelPaypalPlan(abort.signal);
      if (!alive.current) return;
      setAsking(false);
      setMessage(result.message || (paidUp ? `Your plan is cancelled and stays active until ${endDate}.` : "Your plan is cancelled."));
      void qc.invalidateQueries({ queryKey: ["billing"] });
    } catch (failure) {
      if (alive.current) setError(failure instanceof Error ? failure.message : "The cancellation could not be confirmed. Refresh this page to check your plan.");
    } finally { lock.current = false; if (alive.current) setBusy(false); }
  }
  return <div className="space-y-3 rounded-xl border p-4">
    <p>Paid with PayPal.</p>
    {!paidUp && !subscription.cancel_at_period_end && <p className="text-sm">PayPal could not take the last payment, so calling is paused. PayPal tries again by itself. You can also cancel this plan and choose a new one.</p>}
    {subscription.cancel_at_period_end
      ? <p>Your plan ends on {endDate}.</p>
      : asking
        ? <div className="space-y-3"><p>{paidUp ? `Cancel your plan? PayPal stops charging now and your plan stays active until ${endDate}.` : "Cancel your plan? PayPal stops charging and the plan ends now."}</p>
          <div className="flex flex-wrap gap-2"><Button disabled={busy} onClick={() => void cancel()}>{busy ? "Cancelling…" : "Yes, cancel"}</Button><Button variant="outline" disabled={busy} onClick={() => setAsking(false)}>Keep my plan</Button></div></div>
        : <Button variant="outline" onClick={() => { setAsking(true); setMessage(""); }}>Cancel subscription</Button>}
    {error && <p role="alert" className="text-sm text-red-700 dark:text-red-400">{error}</p>}
    {message && <p role="status" className="text-sm">{message}</p>}
  </div>;
}
