// Phase 7 universal-auth-state: this module is the single writer/reader
// of `localStorage["talklee.auth.token"]`, the Bearer-fallback storage
// used for environments where the HttpOnly `talky_at` cookie isn't
// carried (admin frontend, future native shell wrappers, some embedded
// webviews). AuthContext is the single in-app caller — components and
// non-AuthContext code paths use `useAccessToken()` / `useAuth()`
// instead. Direct callers outside auth-context.tsx are forbidden and
// blocked by the structural test added in Phase 8.
//
// AH-Phase-F: the Bearer fallback is now gated by
// `NEXT_PUBLIC_BEARER_FALLBACK`. Default ON for backwards-compat (the
// Ask-AI WebSocket relies on the in-memory accessToken to send the
// first-frame auth — turning the flag off without overhauling the WS
// breaks Ask-AI). Operators who don't use WebSocket auth (cookie-only
// flows) can set `NEXT_PUBLIC_BEARER_FALLBACK=false` to eliminate the
// localStorage XSS surface entirely; in that mode this module becomes
// read-only and the in-memory `accessToken` state in AuthContext is
// the only place a JWT ever lives client-side.
const STORAGE_KEY = "talklee.auth.token";
const LEGACY_COOKIE_NAME = "talklee_auth_token";

function bearerFallbackEnabled(): boolean {
    // Default ON. Only the literal string "false" turns it off, so a
    // typo / unset env never accidentally disables the fallback.
    if (typeof process === "undefined") return true;
    const raw = process.env.NEXT_PUBLIC_BEARER_FALLBACK;
    if (raw === undefined || raw === null) return true;
    return raw.toLowerCase() !== "false";
}

export function isBearerFallbackEnabled(): boolean {
    return bearerFallbackEnabled();
}

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
    // Always READ — even when the fallback is disabled, we still need
    // to surface any value lingering from a prior session so the
    // operator can flip the flag without forcing existing users to
    // re-login. The next setBrowserAuthToken(null) call (logout) or
    // setBrowserAuthToken(newToken) (login → no-op when disabled)
    // either clears or no-ops, draining the storage naturally.
    try {
        const v = window.localStorage.getItem(STORAGE_KEY);
        if (v && v.trim().length > 0) return v;
    } catch {}
    return null;
}

export function setBrowserAuthToken(token: string | null) {
    if (typeof window === "undefined") return;
    // Clearing (logout) always runs — leaving a stale token in storage
    // would defeat the security intent.
    if (token === null) {
        try { window.localStorage.removeItem(STORAGE_KEY); } catch {}
        return;
    }
    // Writing a fresh token honours the Phase F opt-in.
    if (!bearerFallbackEnabled()) return;
    try {
        window.localStorage.setItem(STORAGE_KEY, token);
    } catch {}
}

