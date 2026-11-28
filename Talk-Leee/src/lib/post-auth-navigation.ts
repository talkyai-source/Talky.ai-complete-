/**
 * The canonical dashboard for an authenticated role.
 *
 * Keeping this independent from `next/navigation` makes login, MFA, passkeys,
 * OAuth-style completions, and registration agree on where an authenticated
 * user belongs by default. Login and sign-up may supply an original same-site
 * path (sign-up reads it in app/auth/register/register-next.ts); other
 * completion callers keep their existing dashboard destination.
 * The homepage Ask AI session flag is handled by the caller and keeps
 * precedence. White-label admins retain their own dashboard routing.
 */
export function postAuthDashboard(role: string | null | undefined, next?: string | null): string {
    if (role === "white_label_admin") return "/white-label/dashboard";
    return safeInternalPath(next) || "/dashboard";
}

/**
 * Returns `next` unchanged when it is a safe same-site path, otherwise "".
 * Shared by login and any page that follows a `next` query value.
 */
export function safeInternalPath(next: string | null | undefined): string {
    if (typeof next !== "string" || next.length > 2048 || next !== next.trim()) return "";
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
                return "";
            }
            const parsed = new URL(candidate, origin);
            if (parsed.origin !== origin || /^\/auth(?:\/|$)/i.test(parsed.pathname)) return "";
            // Dot segments can collapse to "//host" (/billing/..//evil.example); API and
            // build-asset paths are never a page to land on after sign-in.
            if (parsed.pathname.startsWith("//") || /^\/(?:api|_next)(?:\/|$)/i.test(parsed.pathname)) return "";
            if (!candidate.includes("%")) return next;
            candidate = decodeURIComponent(candidate);
        }
    } catch {
        // Malformed escapes or URLs do not prevent an otherwise valid login.
    }
    return "";
}
