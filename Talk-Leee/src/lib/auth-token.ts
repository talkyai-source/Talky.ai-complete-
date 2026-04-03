const STORAGE_KEY = "talklee.auth.token";
const LEGACY_COOKIE_NAME = "talklee_auth_token";
const BACKEND_SESSION_COOKIE_NAME = "talky_sid";
const LEGACY_DEV_TOKEN = "dev-token";

export function authTokenStorageKey() {
    return STORAGE_KEY;
}

export function authTokenCookieName() {
    return LEGACY_COOKIE_NAME;
}

export function backendSessionCookieName() {
    return BACKEND_SESSION_COOKIE_NAME;
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
        if (v && v.trim().length > 0) {
            if (v.trim() === LEGACY_DEV_TOKEN) {
                setBrowserAuthToken(null);
                return null;
            }
            return v;
        }
    } catch {}
    const cookie = readCookie(LEGACY_COOKIE_NAME);
    if (cookie && cookie.trim().length > 0) {
        if (cookie.trim() === LEGACY_DEV_TOKEN) {
            setBrowserAuthToken(null);
            return null;
        }
        try {
            window.localStorage.setItem(STORAGE_KEY, cookie);
        } catch {}
        const isProd = process.env.NODE_ENV === "production";
        document.cookie = `${LEGACY_COOKIE_NAME}=; Path=/; Max-Age=0; SameSite=Lax${isProd ? "; Secure" : ""}`;
        return cookie;
    }
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
    document.cookie = `${LEGACY_COOKIE_NAME}=; Path=/; Max-Age=0; SameSite=Lax${isProd ? "; Secure" : ""}`;
}
