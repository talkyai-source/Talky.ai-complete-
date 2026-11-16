// The customer-facing account state GET /auth/me sends (production-readiness
// 3.6): "active" | "suspended_subscription" | "suspended_hold"
// (backend/app/api/v1/endpoints/auth/schemas.py:118). A missing value means active.

/** The server's own sentence for a suspended subscription: its campaign Start
 *  refusal (backend/app/api/v1/endpoints/campaigns.py:178-181). */
export const SUBSCRIPTION_SUSPENDED_TEXT =
    "This account is suspended, so campaigns cannot start. Billing and Support stay available.";

/** Admin "Suspend": sign-in works and the server refuses only campaign Start. */
export function isSubscriptionSuspended(user: { account_state?: string | null } | null | undefined): boolean {
    return user?.account_state === "suspended_subscription";
}
