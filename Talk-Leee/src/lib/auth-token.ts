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

