import { NextResponse, type NextRequest } from "next/server";
import { authTokenCookieName } from "./lib/auth-token";

function setSecurityHeaders(res: NextResponse, input: { csp: string; inProd: boolean; https: boolean }) {
    res.headers.set("Content-Security-Policy", input.csp);
    res.headers.set("X-Content-Type-Options", "nosniff");
    res.headers.set("Referrer-Policy", "strict-origin-when-cross-origin");
    res.headers.set("X-Frame-Options", "DENY");
    res.headers.set("Cross-Origin-Opener-Policy", "same-origin");
    res.headers.set("Cross-Origin-Resource-Policy", "same-origin");
    res.headers.set("X-DNS-Prefetch-Control", "off");
    res.headers.set("X-Permitted-Cross-Domain-Policies", "none");
    if (input.inProd && input.https) res.headers.set("Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload");
}

function devBypassAuth() {
    if (process.env.NODE_ENV === "production") return false;
    return process.env.TALKLEE_REQUIRE_AUTH !== "1";
}

function isHttps(req: NextRequest) {
    const forwarded = req.headers.get("x-forwarded-proto");
    if (forwarded) return forwarded.split(",")[0]!.trim().toLowerCase() === "https";
    return req.nextUrl.protocol === "https:";
}

function redirectToHttps(req: NextRequest) {
    const url = req.nextUrl.clone();
    url.protocol = "https:";
    const host = req.headers.get("host");
    if (host) url.host = host;
    return NextResponse.redirect(url, 308);
}

function isLocalHost(req: NextRequest) {
    const hostHeader = req.headers.get("host") ?? "";
    const host = hostHeader.split(":")[0]?.trim().toLowerCase() || req.nextUrl.hostname.toLowerCase();
    return host === "localhost" || host === "127.0.0.1" || host === "[::1]";
}

function base64Nonce() {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    if (typeof btoa === "function") {
        let s = "";
        for (const b of bytes) s += String.fromCharCode(b);
        return btoa(s);
    }
    const BufferCtor = (globalThis as unknown as { Buffer?: { from: (data: Uint8Array) => { toString: (enc: string) => string } } }).Buffer;
    if (BufferCtor) return BufferCtor.from(bytes).toString("base64");
    return Array.from(bytes)
        .map((b) => b.toString(16).padStart(2, "0"))
        .join("");
}

function originFromUrl(raw: string | undefined) {
    if (!raw) return undefined;
    try {
        return new URL(raw).origin;
    } catch {
        return undefined;
    }
}

function sentryOriginFromDsn(raw: string | undefined) {
    if (!raw) return undefined;
    try {
        return new URL(raw).origin;
    } catch {
        return undefined;
    }
}

function isPublicPath(pathname: string) {
    if (pathname === "/") return true;
    if (pathname.startsWith("/auth/")) return true;
    if (pathname === "/auth") return true;
    if (pathname.startsWith("/connectors/callback")) return true;
    if (pathname.startsWith("/_next/")) return true;
    return false;
}

function readCookieFromHeader(req: NextRequest, name: string) {
    const raw = req.headers.get("cookie");
    if (!raw) return undefined;
    const parts = raw.split(";").map((p) => p.trim());
    for (const part of parts) {
        if (!part) continue;
        const eq = part.indexOf("=");
        if (eq <= 0) continue;
        const k = part.slice(0, eq).trim();
        if (k !== name) continue;
        const v = part.slice(eq + 1).trim();
        try {
            return decodeURIComponent(v);
        } catch {
            return v;
        }
    }
    return undefined;
}

export function middleware(req: NextRequest) {
    const { pathname, search } = req.nextUrl;

    const inProd = process.env.NODE_ENV === "production";
    const https = isHttps(req);

    if (inProd && !https && !isLocalHost(req)) {
        const res = redirectToHttps(req);
        setSecurityHeaders(res, { csp: "default-src 'self'", inProd, https });
        return res;
    }

    const nonce = base64Nonce();
    const isDev = !inProd;

    const apiOrigin = originFromUrl(process.env.NEXT_PUBLIC_API_BASE_URL);
    const sentryOrigin = sentryOriginFromDsn(process.env.NEXT_PUBLIC_SENTRY_DSN);

    const connectSrc = [
        "'self'",
        apiOrigin,
        sentryOrigin,
        "https:",
        "wss:",
        isDev ? "http:" : undefined,
        isDev ? "ws:" : undefined,
    ]
        .filter(Boolean)
        .join(" ");

    const csp = [
        "default-src 'self'",
        `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        "connect-src " + connectSrc,
        "media-src 'self' data: blob:",
        "worker-src 'self' blob:",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "upgrade-insecure-requests",
        "block-all-mixed-content",
    ].join("; ");

    const requestHeaders = new Headers(req.headers);
    requestHeaders.set("content-security-policy", csp);
    requestHeaders.set("x-nonce", nonce);

    const token = readCookieFromHeader(req, authTokenCookieName());
    if (token && token.trim().length > 0) {
        const res = NextResponse.next({ request: { headers: requestHeaders } });
        setSecurityHeaders(res, { csp, inProd, https });
        return res;
    }

    if (isLocalHost(req)) {
        const res = NextResponse.next({ request: { headers: requestHeaders } });
        setSecurityHeaders(res, { csp, inProd, https });
        return res;
    }

    if (devBypassAuth()) {
        const res = NextResponse.next({ request: { headers: requestHeaders } });
        res.cookies.set({
            name: authTokenCookieName(),
            value: "dev-token",
            path: "/",
            sameSite: "lax",
            httpOnly: false,
            secure: false,
            maxAge: 60 * 60 * 24 * 7,
        });
        setSecurityHeaders(res, { csp, inProd, https });
        return res;
    }

    if (isPublicPath(pathname)) {
        const res = NextResponse.next({ request: { headers: requestHeaders } });
        setSecurityHeaders(res, { csp, inProd, https });
        return res;
    }

    const url = req.nextUrl.clone();
    url.pathname = "/auth/login";
    url.searchParams.set("next", `${pathname}${search}`);
    const res = NextResponse.redirect(url);
    setSecurityHeaders(res, { csp, inProd, https });
    return res;
}

export const config = {
    matcher: ["/", "/((?!.*\\..*).*)"],
};
