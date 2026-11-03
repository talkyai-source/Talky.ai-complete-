import { useSyncExternalStore } from "react";

/**
 * Purchase refusals the backend only reports AFTER a click (no pre-click
 * signal exists; see BACKEND GAP G7). Once the backend has refused a purchase
 * for this session, every button that refusal applies to is disabled and the
 * backend's own message is shown, instead of offering the same purchase again.
 *
 * Scope rules (from backend/app/api/v1/endpoints/billing.py and
 * billing_topups.py):
 *   - test_billing_team_only (403) applies to plans and top-ups  -> "all"
 *   - tenant_unavailable (403) applies to plans and top-ups      -> "all"
 *   - billing_unavailable (503) applies to plan checkout          -> "plan"
 *   - "Minute top-ups are not available yet" (503)               -> "topup"
 */
export type BillingRefusalScope = "all" | "plan" | "topup";

export interface BillingRefusal {
  code: string;
  message: string;
  scope: BillingRefusalScope;
}

let current: BillingRefusal | null = null;
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

export function recordBillingRefusal(refusal: BillingRefusal): void {
  current = refusal;
  emit();
}

/** Test-only reset; production code never clears a recorded refusal. */
export function resetBillingRefusal(): void {
  current = null;
  emit();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

function snapshot() {
  return current;
}

/** The refusal that applies to `scope`, or null when the backend has not refused. */
export function billingRefusalFor(scope: Exclude<BillingRefusalScope, "all">): BillingRefusal | null {
  if (!current) return null;
  return current.scope === "all" || current.scope === scope ? current : null;
}

export function useBillingRefusal(scope: Exclude<BillingRefusalScope, "all">): BillingRefusal | null {
  const value = useSyncExternalStore(subscribe, snapshot, snapshot);
  if (!value) return null;
  return value.scope === "all" || value.scope === scope ? value : null;
}

/**
 * Map a backend refusal to its scope, or null when the error is not one of
 * the known pre-purchase refusals (those are shown but do not disable).
 */
export function classifyBillingRefusal(status: number | undefined, code: string | undefined, message: string | undefined): BillingRefusal | null {
  if (status === 403 && (code === "test_billing_team_only" || code === "tenant_unavailable")) {
    return { code, message: message || "", scope: "all" };
  }
  if (status === 503 && code === "billing_unavailable") {
    return { code, message: message || "", scope: "plan" };
  }
  if (status === 503 && message === "Minute top-ups are not available yet") {
    return { code: "topups_unavailable", message, scope: "topup" };
  }
  return null;
}
