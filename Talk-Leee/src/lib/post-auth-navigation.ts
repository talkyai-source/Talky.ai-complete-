/**
 * The canonical dashboard for an authenticated role.
 *
 * Keeping this independent from `next/navigation` makes login, MFA, passkeys,
 * OAuth-style completions, and registration agree on where an authenticated
 * user belongs by default. Login may supply an original same-site path;
 * other completion callers keep their existing dashboard destination.
 * The homepage Ask AI session flag is handled by the caller and keeps
 * precedence. White-label admins retain their own dashboard routing.
 */
export function postAuthDashboard(role: string | null | undefined, next?: string | null): string {
    if (role === "white_label_admin") return "/white-label/dashboard";
    if (typeof next !== "string" || next.length > 2048 || next !== next.trim()) return "/dashboard";
    // Validate decoded forms too: browsers/proxies must not reinterpret an
    // encoded slash, backslash, control character or auth route after login.
    // Keep the original query/fragment on success; never navigate a decoded URL.
    const origin = "https://return-target.invalid";
    let candidate = next;
    try {
        for (let depth = 0; depth < 4; depth++) {
            const unsafeCharacter = Array.from(candidate).some((character) => {
                const code = character.charCodeAt(0);
                return character === "\\" || code < 32 || (code >= 127 && code <= 159);
            });
            if (!candidate.startsWith("/") || candidate.startsWith("//") || unsafeCharacter) {
                return "/dashboard";
            }
            const parsed = new URL(candidate, origin);
            if (parsed.origin !== origin || /^\/auth(?:\/|$)/i.test(parsed.pathname)) return "/dashboard";
            if (!candidate.includes("%")) return next;
            candidate = decodeURIComponent(candidate);
        }
    } catch {
        // Malformed escapes or URLs do not prevent an otherwise valid login.
    }
    return "/dashboard";
}
