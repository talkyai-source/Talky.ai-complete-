/**
 * The one canonical landing page after authentication.
 *
 * Keeping this independent from `next/navigation` makes login, MFA, passkeys,
 * OAuth-style completions, and registration agree on where an authenticated
 * user belongs. Query-string return targets are intentionally ignored: a
 * successful authentication always opens the appropriate dashboard.
 */
export function postAuthDashboard(role: string | null | undefined): string {
    return role === "white_label_admin"
        ? "/white-label/dashboard"
        : "/dashboard";
}
