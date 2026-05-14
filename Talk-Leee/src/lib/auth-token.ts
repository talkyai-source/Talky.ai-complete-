// DEPRECATED — kept only during the Phase A→C cookie migration.
//
// Phase A switched the backend to issue httpOnly `talky_at` (15min) +
// `talky_rt` (7d) cookies on every successful auth. Phase B made the
// HTTP client send `credentials: 'include'` and silently rotate via
// `/auth/refresh`. The localStorage key and non-httpOnly mirror cookie
// below predate that and are only read so users mid-migration with an
// existing Bearer token still bootstrap cleanly.
//
// REMOVE AFTER SOAK: once Phase A+B have been in production long enough
// for every active session (24h max) to have rotated, delete this file
// plus every importer found via `grep authTokenCookieName`.
const STORAGE_KEY = "talklee.auth.token";
const COOKIE_NAME = "talklee_auth_token";

export function authTokenStorageKey() {
    return STORAGE_KEY;
}

export function authTokenCookieName() {
    return COOKIE_NAME;
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

export function getBrowserAuthToken(): string | null {
    if (typeof window === "undefined") return null;
    try {
        const v = window.localStorage.getItem(STORAGE_KEY);
        if (v && v.trim().length > 0) return v;
    } catch {}
    const cookie = readCookie(COOKIE_NAME);
    if (cookie && cookie.trim().length > 0) return cookie;
    return null;
}

export function setBrowserAuthToken(token: string | null) {
    if (typeof window === "undefined") return;
    try {
        if (token) window.localStorage.setItem(STORAGE_KEY, token);
        else window.localStorage.removeItem(STORAGE_KEY);
    } catch {}

    if (typeof document === "undefined") return;
    const isProd = process.env.NODE_ENV === "production";
    if (!token) {
        document.cookie = `${COOKIE_NAME}=; Path=/; Max-Age=0; SameSite=Lax${isProd ? "; Secure" : ""}`;
        return;
    }
    document.cookie = `${COOKIE_NAME}=${encodeURIComponent(token)}; Path=/; Max-Age=${60 * 60 * 24 * 7}; SameSite=Lax${isProd ? "; Secure" : ""}`;
}

