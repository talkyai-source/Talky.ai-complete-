"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { isApiClientError } from "@/lib/http-client";
import {
  approvedCheckoutUrl, readBillingCheckout, readSavedPurchase, requireBillingBackend,
  startBillingCheckout, storeSavedPurchase, validateOutstandingAttempt, type BillingCatalog, type BillingPrice,
  type CheckoutAttempt, type SavedPurchase,
} from "@/lib/billing-purchase";

export const purchaseErrorText = (error: unknown) => error instanceof Error ? error.message : "Receipt is unconfirmed. Check the saved purchase again.";
export function useBillingPurchase(scope: string, onNavigate: (url: string) => void) {
  const qc = useQueryClient();
  const [saved, setSaved] = useState<SavedPurchase | null>(null);
  const [attempt, setAttempt] = useState<CheckoutAttempt | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);
  const [storageAvailable, setStorageAvailable] = useState(true);
  const [returned, setReturned] = useState<string | null>(null);
  const current = useRef<SavedPurchase | null>(null);
  const inFlight = useRef(false);
  const alive = useRef(true);
  const requestController = useRef<AbortController | null>(null);
  const receive = useCallback((result: CheckoutAttempt) => {
    if (!alive.current) return;
    setAttempt(result); setError("");
    if (["activated", "expired", "failed"].includes(result.state)) {
      setStorageAvailable(storeSavedPurchase(scope, null));
      current.current = null; setSaved(null);
      void qc.invalidateQueries({ queryKey: ["billing"] });
    }
  }, [scope, qc]);
  const check = useCallback(async () => {
    if (!current.current || inFlight.current) return;
    inFlight.current = true; setBusy(true); setError("");
    const controller = new AbortController(); requestController.current = controller;
    try { receive(await readBillingCheckout(current.current, controller.signal)); }
    catch (failure) { if (alive.current) setError(purchaseErrorText(failure)); }
    finally { inFlight.current = false; if (alive.current) setBusy(false); }
  }, [receive]);
  useEffect(() => {
    alive.current = true;
    const restored = readSavedPurchase(scope);
    current.current = restored.saved;
    // Restore browser state after hydration, never into server-rendered HTML.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSaved(restored.saved);
    setStorageAvailable(restored.available);
    setReturned(new URLSearchParams(window.location.search).get("checkout"));
    setReady(true);
    void check(); // Read-only reconciliation, never create on reload/return.
    return () => { alive.current = false; requestController.current?.abort(); };
  }, [scope, check]);

  const run = async (selection?: { plan: BillingCatalog[number]; price: BillingPrice }) => {
    if (!ready || inFlight.current) return;
    inFlight.current = true; setBusy(true); setError("");
    try {
      requireBillingBackend();
      let frozen = current.current;
      if (!frozen) {
        if (!selection || !storageAvailable) throw new Error("Enable session storage and reload before starting a purchase, so a lost response can be recovered safely.");
        const { plan, price } = selection;
        frozen = { request_id: crypto.randomUUID(), price_option: { ...price, plan_id: plan.id, plan_name: plan.name } };
        if (!storeSavedPurchase(scope, frozen)) throw new Error("The browser could not save the request. Nothing was submitted. Enable session storage and try again.");
        current.current = frozen; setSaved(frozen);
      }
      const controller = new AbortController(); requestController.current = controller;
      const result = await startBillingCheckout(frozen, controller.signal);
      if (!alive.current) return;
      receive(result);
      if (result.state === "open" && result.checkout_url) onNavigate(approvedCheckoutUrl(result.checkout_url));
    } catch (failure) {
      if (alive.current) {
        setError(purchaseErrorText(failure));
        // Only explicit pre-insert proof can release this request. Another
        // tab's outstanding purchase is adopted, never automatically submitted.
        if (isApiClientError(failure) && failure.status !== undefined && failure.status >= 400 && failure.status < 500) {
          const details = failure.details && typeof failure.details === "object" ? failure.details as Record<string, unknown> : null;
          if (details?.request_not_started === true && Object.hasOwn(details, "existing_attempt")) {
            try {
              const existing = validateOutstandingAttempt(details.existing_attempt);
              const restored = { request_id: existing.request_id, price_option: existing.price_option };
              if (!storeSavedPurchase(scope, restored)) throw new Error("The existing purchase could not be saved. Keep your current reference and contact support before retrying.");
              current.current = restored; setSaved(restored); setAttempt(existing); setStorageAvailable(true);
            } catch (invalidReceipt) { setError(purchaseErrorText(invalidReceipt)); }
          } else if (details?.request_not_started === true) {
            setStorageAvailable(storeSavedPurchase(scope, null));
            current.current = null; setSaved(null); setAttempt(null);
          }
        }
      }
    }
    finally { inFlight.current = false; if (alive.current) setBusy(false); }
  };
  return { saved, attempt, error, busy, ready, storageAvailable, returned, run, check };
}
