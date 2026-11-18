"use client";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { isApiClientError } from "@/lib/http-client";
import { classifyBillingRefusal, recordBillingRefusal } from "@/lib/billing-refusal";
import {
  approvedCheckoutUrl, checkoutRequestIdFromUrl, readBillingCheckout, readBillingCheckoutById, readSavedPurchase,
  requireBillingBackend, startBillingCheckout, storeSavedPurchase, validateOutstandingAttempt, type BillingCatalog,
  type BillingPrice, type CheckoutAttempt, type SavedPurchase,
} from "@/lib/billing-purchase";
import { paypalPlanReturnFromUrl, readPaypalPlanCheckout, startPaypalPlanCheckout } from "@/lib/paypal-plan-api";

export type PurchaseProvider = "stripe" | "paypal";

export const purchaseErrorText = (error: unknown) => error instanceof Error ? error.message : "Receipt is unconfirmed. Check the saved purchase again.";
/** After ?checkout=success the server reconciles the Stripe session on each
 * read (billing_checkout.py get); poll it until a terminal state or deadline. */
const POLL_INTERVAL_MS = 3_000;
const POLL_DEADLINE_MS = 120_000;
const terminal = (state: CheckoutAttempt["state"]) => ["activated", "expired", "failed"].includes(state);
/** Whether a ?checkout=success return is still being confirmed with Stripe.
 * The plan tile on /billing (a sibling of the return notice) reads this so it
 * never shows the old plan, "Inactive" or "No plan selected" meanwhile. */
let confirmationPending = false;
let confirmationProvider: PurchaseProvider = "stripe";
const confirmationListeners = new Set<() => void>();
export function setCheckoutConfirmationPending(pending: boolean, provider: PurchaseProvider = "stripe") {
  if (confirmationPending === pending && (!pending || confirmationProvider === provider)) return;
  confirmationPending = pending;
  if (pending) confirmationProvider = provider;
  for (const listener of confirmationListeners) listener();
}
const subscribeConfirmation = (listener: () => void) => { confirmationListeners.add(listener); return () => { confirmationListeners.delete(listener); }; };
const readConfirmation = () => confirmationPending;
export function useCheckoutConfirmationPending() {
  return useSyncExternalStore(subscribeConfirmation, readConfirmation, () => false);
}
/** Which provider the return being confirmed came from (for wording only). */
export function useCheckoutConfirmationProvider(): PurchaseProvider {
  return useSyncExternalStore(subscribeConfirmation, () => confirmationProvider, () => "stripe");
}
/** An outstanding attempt the server reported: its link is checked against the provider of the flow in progress first. */
function adoptableAttempt(data: unknown, preferred: PurchaseProvider): { attempt: CheckoutAttempt; provider: PurchaseProvider } {
  const order: PurchaseProvider[] = preferred === "paypal" ? ["paypal", "stripe"] : ["stripe", "paypal"];
  let failure: unknown;
  for (const provider of order) {
    try { return { attempt: validateOutstandingAttempt(data, provider === "paypal" ? "paypal" : "checkout"), provider }; }
    catch (error) { failure = error; }
  }
  throw failure;
}
const savedFor = (attempt: CheckoutAttempt, provider: PurchaseProvider): SavedPurchase =>
  ({ request_id: attempt.request_id, price_option: attempt.price_option, ...(provider === "paypal" ? { provider } : {}) });
export function useBillingPurchase(scope: string, onNavigate: (url: string) => void) {
  const qc = useQueryClient();
  const [saved, setSaved] = useState<SavedPurchase | null>(null);
  const [attempt, setAttempt] = useState<CheckoutAttempt | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);
  const [storageAvailable, setStorageAvailable] = useState(true);
  const [returned, setReturned] = useState<string | null>(null);
  const [pendingConfirmation, setPendingConfirmation] = useState(false);
  const [provider, setProvider] = useState<PurchaseProvider>("stripe");
  const returnProvider = useRef<PurchaseProvider>("stripe");
  const cancelledAtPaypal = useRef(false);
  const current = useRef<SavedPurchase | null>(null);
  const urlRequestId = useRef<string | null>(null);
  const poll = useRef<{ interval: ReturnType<typeof setInterval>; deadline: ReturnType<typeof setTimeout> } | null>(null);
  const inFlight = useRef(false);
  const alive = useRef(true);
  const requestController = useRef<AbortController | null>(null);
  const stopPolling = useCallback(() => {
    if (poll.current) { clearInterval(poll.current.interval); clearTimeout(poll.current.deadline); poll.current = null; }
    setCheckoutConfirmationPending(false);
    if (alive.current) setPendingConfirmation(false);
  }, []);
  const receive = useCallback((result: CheckoutAttempt) => {
    if (!alive.current) return;
    setAttempt(result); setError("");
    if (terminal(result.state)) {
      stopPolling();
      setStorageAvailable(storeSavedPurchase(scope, null));
      current.current = null; setSaved(null);
      void qc.invalidateQueries({ queryKey: ["billing"] });
    }
  }, [scope, qc, stopPolling]);
  const check = useCallback(async () => {
    const reference = urlRequestId.current;
    if ((!current.current && !reference) || inFlight.current) return;
    inFlight.current = true; setBusy(true); setError("");
    const controller = new AbortController(); requestController.current = controller;
    try {
      if (current.current) {
        const saved = current.current;
        receive(saved.provider === "paypal" ? await readPaypalPlanCheckout(saved.request_id, controller.signal) : await readBillingCheckout(saved, controller.signal));
      } else {
        const viaPaypal = returnProvider.current === "paypal";
        const result = viaPaypal ? await readPaypalPlanCheckout(reference!, controller.signal) : await readBillingCheckoutById(reference!, controller.signal);
        if (!alive.current) return;
        // Adopt the server's own record of this return so it can be checked or resumed; never submit it automatically.
        // A purchase the customer backed out of at PayPal and the server still shows as unapproved is left alone,
        // so the plan buttons work again (the server drops it when a new purchase starts).
        if (!terminal(result.state) && !(cancelledAtPaypal.current && result.state === "open")) {
          const adopted = savedFor(result, returnProvider.current);
          setStorageAvailable(storeSavedPurchase(scope, adopted)); current.current = adopted; setSaved(adopted);
        }
        receive(result);
      }
    } catch (failure) {
      if (alive.current) {
        setError(purchaseErrorText(failure));
        // A definite answer (404 checkout_not_found, 403) will not change by asking again.
        if (isApiClientError(failure) && failure.status !== undefined && failure.status >= 400 && failure.status < 500) stopPolling();
      }
    }
    finally { inFlight.current = false; if (alive.current) setBusy(false); }
  }, [receive, scope, stopPolling]);
  useEffect(() => {
    alive.current = true;
    const restored = readSavedPurchase(scope);
    current.current = restored.saved;
    const params = new URLSearchParams(window.location.search);
    const paypalReturn = paypalPlanReturnFromUrl(window.location.search);
    // A PayPal return is handled only as one: the Stripe return rules must not fire for it.
    const paypalParams = params.get("provider") === "paypal";
    const checkout = paypalParams && !paypalReturn ? null : params.get("checkout");
    const wasPaypal = paypalReturn !== null || restored.saved?.provider === "paypal";
    returnProvider.current = wasPaypal ? "paypal" : "stripe";
    urlRequestId.current = restored.saved ? null : paypalReturn ? paypalReturn.requestId : paypalParams ? null : checkoutRequestIdFromUrl(window.location.search);
    cancelledAtPaypal.current = paypalReturn?.outcome === "cancelled";
    if (paypalReturn?.outcome === "cancelled" && restored.saved?.request_id === paypalReturn.requestId) {
      // The customer backed out at PayPal. Forget the saved copy of this very request and ask the server what
      // became of it: only its answer says whether anything was charged.
      storeSavedPurchase(scope, null); current.current = null; restored.saved = null;
      urlRequestId.current = paypalReturn.requestId;
    }
    // Restore browser state after hydration, never into server-rendered HTML.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSaved(restored.saved);
    setStorageAvailable(restored.available);
    setReturned(checkout);
    setProvider(wasPaypal ? "paypal" : "stripe");
    setReady(true);
    if (checkout === "success" && (restored.saved || urlRequestId.current)) {
      poll.current = { interval: setInterval(() => void check(), POLL_INTERVAL_MS), deadline: setTimeout(stopPolling, POLL_DEADLINE_MS) };
      setPendingConfirmation(true); setCheckoutConfirmationPending(true, wasPaypal ? "paypal" : "stripe");
    }
    void check(); // Read-only reconciliation, never create on reload/return.
    return () => { alive.current = false; stopPolling(); requestController.current?.abort(); };
  }, [scope, check, stopPolling]);

  const run = async (selection?: { plan: BillingCatalog[number]; price: BillingPrice }, via: PurchaseProvider = "stripe") => {
    if (!ready || inFlight.current) return;
    inFlight.current = true; setBusy(true); setError("");
    try {
      requireBillingBackend();
      let frozen = current.current;
      if (!frozen) {
        if (!selection || !storageAvailable) throw new Error("Enable session storage and reload before starting a purchase, so a lost response can be recovered safely.");
        const { plan, price } = selection;
        frozen = { request_id: crypto.randomUUID(), price_option: { ...price, plan_id: plan.id, plan_name: plan.name }, ...(via === "paypal" ? { provider: "paypal" as const } : {}) };
        if (!storeSavedPurchase(scope, frozen)) throw new Error("The browser could not save the request. Nothing was submitted. Enable session storage and try again.");
        current.current = frozen; setSaved(frozen);
      }
      const controller = new AbortController(); requestController.current = controller;
      const viaPaypal = frozen.provider === "paypal";
      returnProvider.current = viaPaypal ? "paypal" : "stripe"; setProvider(returnProvider.current);
      const result = viaPaypal ? await startPaypalPlanCheckout(frozen, controller.signal) : await startBillingCheckout(frozen, controller.signal);
      if (!alive.current) return;
      receive(result);
      if (result.state === "open" && result.checkout_url) onNavigate(approvedCheckoutUrl(result.checkout_url, viaPaypal ? "paypal" : "checkout"));
    } catch (failure) {
      if (alive.current) {
        setError(purchaseErrorText(failure));
        // Only explicit pre-insert proof can release this request. Another
        // tab's outstanding purchase is adopted, never automatically submitted.
        if (isApiClientError(failure) && failure.status !== undefined && failure.status >= 400 && failure.status < 500) {
          const details = failure.details && typeof failure.details === "object" ? failure.details as Record<string, unknown> : null;
          if (details?.request_not_started === true && Object.hasOwn(details, "existing_attempt")) {
            try {
              const { attempt: existing, provider: existingProvider } = adoptableAttempt(details.existing_attempt, returnProvider.current);
              const restored = savedFor(existing, existingProvider);
              returnProvider.current = existingProvider; setProvider(existingProvider);
              if (!storeSavedPurchase(scope, restored)) throw new Error("The existing purchase could not be saved. Keep your current reference and contact support before retrying.");
              current.current = restored; setSaved(restored); setAttempt(existing); setStorageAvailable(true);
            } catch (invalidReceipt) { setError(purchaseErrorText(invalidReceipt)); }
          } else if (details?.request_not_started === true) {
            setStorageAvailable(storeSavedPurchase(scope, null));
            current.current = null; setSaved(null); setAttempt(null);
          }
        }
        // The backend only reports these refusals after a click; remember the
        // refusal so the same purchase is not offered again this session.
        if (isApiClientError(failure)) {
          const refusal = classifyBillingRefusal(failure.status, failure.code, failure.message);
          if (refusal) recordBillingRefusal(refusal);
        }
      }
    }
    finally { inFlight.current = false; if (alive.current) setBusy(false); }
  };
  return { saved, attempt, error, busy, ready, storageAvailable, returned, pendingConfirmation, provider, run, check };
}
