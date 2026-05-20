// Phase 7 universal-auth-state: this module is the single writer/reader
// of `localStorage["talklee.auth.token"]`, the Bearer-fallback storage
// used for environments where the HttpOnly `talky_at` cookie isn't
// carried (admin frontend, future native shell wrappers, some embedded
// webviews). AuthContext is the single in-app caller — components and
// non-AuthContext code paths use `useAccessToken()` / `useAuth()`
// instead. Direct callers outside auth-context.tsx are forbidden and
// blocked by the structural test added in Phase 8.
//
// What changed from Phase A→C plumbing:
//   - The non-HttpOnly mirror cookie `talklee_auth_token` is no longer
//     written. `setBrowserAuthToken` now only touches localStorage.
//   - The cookie is still READ once during AuthContext bootstrap so
//     pre-deploy sessions can be migrated to localStorage (and the
//     legacy cookie cleared). After the 2-week soak, both the cookie
//     READ in AuthContext and `authTokenCookieName` / `readLegacyCookie`
//     can be deleted entirely.
const STORAGE_KEY = "talklee.auth.token";
const LEGACY_COOKIE_NAME = "talklee_auth_token";

export function authTokenStorageKey() {
    return STORAGE_KEY;
}

export function authTokenCookieName() {
    return LEGACY_COOKIE_NAME;
}

function readCookie(name: string) {
    if (typeof document === "undefined") return null;
    const parts = document.cookie.split(";").map((p) => p.trim());
    for (const part of parts) {
        if (!part) continue;
        const eq = part.indexOf("=");
        if (eq <= 0) continue;
        const k = part.slice(0, eq);
        if (k !== name) continue;
        const v = part.slice(eq + 1);
        try {
            return decodeURIComponent(v);
        } catch {
            return v;
        }
    }
    return null;
}

/**
 * Phase 7 migration helper. AuthContext calls this exactly once on
 * mount: if a legacy `talklee_auth_token` cookie is present from a
 * pre-Phase-7 session, surface its value (AuthContext will commit it
 * into the canonical localStorage key) and clear the cookie. Returns
 * null when no legacy cookie exists, which is the steady-state path
 * after the 2-week soak.
 */
export function consumeLegacyAuthCookie(): string | null {
    if (typeof document === "undefined") return null;
    const value = readCookie(LEGACY_COOKIE_NAME);
    if (!value || !value.trim()) return null;
    const isProd = process.env.NODE_ENV === "production";
    document.cookie = `${LEGACY_COOKIE_NAME}=; Path=/; Max-Age=0; SameSite=Lax${isProd ? "; Secure" : ""}`;
    return value;
}

export function getBrowserAuthToken(): string | null {
    if (typeof window === "undefined") return null;
    try {
        const v = window.localStorage.getItem(STORAGE_KEY);
        if (v && v.trim().length > 0) return v;
    } catch {}
    return null;
}

export function setBrowserAuthToken(token: string | null) {
    if (typeof window === "undefined") return;
    try {
        if (token) window.localStorage.setItem(STORAGE_KEY, token);
        else window.localStorage.removeItem(STORAGE_KEY);
    } catch {}
}

