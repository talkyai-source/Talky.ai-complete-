/**
 * The canonical dashboard for an authenticated role.
 *
 * Keeping this independent from `next/navigation` makes login, MFA, passkeys,
 * OAuth-style completions, and registration agree on where an authenticated
 * user belongs. Query-string return targets are intentionally ignored: no
 * URL can choose the destination. The one exception to "always the
 * dashboard" is the homepage return after an arrival from the homepage Ask AI
 * button, which login-client.tsx and register-client.tsx decide from the
 * per-tab session flag in askai-signin-notice.tsx (never from a URL), and
 * which never applies to a white-label admin.
 */
export function postAuthDashboard(role: string | null | undefined): string {
    return role === "white_label_admin"
        ? "/white-label/dashboard"
        : "/dashboard";
}
