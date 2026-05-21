import { NextResponse, type NextRequest } from "next/server";
import { authTokenCookieName } from "./lib/auth-token";

const INTERNAL_BYPASS_HEADER = "x-talklee-mw-internal";
const WHITE_LABEL_ADMIN_ROLE = "white_label_admin";
const PARTNER_ADMIN_ROLE = "partner_admin";
const WHITE_LABEL_DASHBOARD_PATH = "/white-label/dashboard";

function setSecurityHeaders(res: NextResponse, input: { csp: string; inProd: boolean; https: boolean }) {
    res.headers.set("Content-Security-Policy", input.csp);
    res.headers.set("X-Content-Type-Options", "nosniff");
    res.headers.set("Referrer-Policy", "strict-origin-when-cross-origin");
    res.headers.set("X-Frame-Options", "DENY");
    res.headers.set("Cross-Origin-Opener-Policy", "same-origin");
    res.headers.set("Cross-Origin-Resource-Policy", "same-origin");
    res.headers.set("X-DNS-Prefetch-Control", "on");
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
    if (pathname === "/ai-voice-dialer" || pathname.startsWith("/ai-voice-dialer/")) return true;
    if (pathname === "/ai-assist" || pathname.startsWith("/ai-assist/")) return true;
    if (pathname === "/ai-voice-agent" || pathname.startsWith("/ai-voice-agent/")) return true;
    if (pathname.startsWith("/use-cases")) return true;
    if (pathname.startsWith("/industries")) return true;
    return false;
}

function isWhiteLabelPath(pathname: string) {
    return pathname === "/white-label" || pathname.startsWith("/white-label/");
}

function isWhiteLabelAdminPath(pathname: string) {
    return pathname === WHITE_LABEL_DASHBOARD_PATH || pathname.startsWith(WHITE_LABEL_DASHBOARD_PATH + "/") || pathname === "/white-label";
}

function isApiPath(pathname: string) {
    return pathname === "/api" || pathname.startsWith("/api/");
}

function isConnectorCallbackPath(pathname: string) {
    return pathname.startsWith("/connectors/callback");
}

function isAdminOrInfrastructurePath(pathname: string) {
    if (pathname === "/admin" || pathname.startsWith("/admin/")) return true;
    if (pathname === "/super-admin" || pathname.startsWith("/super-admin/")) return true;
    if (pathname === "/system" || pathname.startsWith("/system/")) return true;
    if (pathname === "/infrastructure" || pathname.startsWith("/infrastructure/")) return true;
    if (pathname === "/internal" || pathname.startsWith("/internal/")) return true;
    if (pathname === "/ops" || pathname.startsWith("/ops/")) return true;
    if (pathname === "/platform" || pathname.startsWith("/platform/")) return true;
    return false;
}

function apiBaseUrlForRequest(req: NextRequest) {
    const configured = process.env.NEXT_PUBLIC_API_BASE_URL;
    if (configured && configured.trim().length > 0) return configured.replace(/\/+$/, "");
    return `${req.nextUrl.origin}/api/v1`;
}

async function fetchUserContextFromBackend(input: { req: NextRequest; cookieHeader: string }): Promise<{ role: string; partnerId: string | null } | null> {
    const baseUrl = apiBaseUrlForRequest(input.req);
    const endpoints = [`${baseUrl}/auth/me`, `${baseUrl}/me`];
    for (const url of endpoints) {
        try {
            const res = await fetch(url, {
                method: "GET",
                headers: {
                    // Forward the full incoming cookie header so the
                    // backend sees BOTH the new httpOnly `talky_at`
                    // access cookie (Phase A) and any legacy cookies.
                    cookie: input.cookieHeader,
                    accept: "application/json",
                    [INTERNAL_BYPASS_HEADER]: "1",
                },
                next: { revalidate: 30 },
            });
            if (!res.ok) continue;
            const data = (await res.json().catch(() => null)) as unknown;
            if (!data || typeof data !== "object") continue;
            const role = (data as { role?: unknown }).role;
            if (typeof role !== "string" || role.trim().length === 0) continue;
            const partnerId =
                (data as { partner_id?: unknown }).partner_id ?? (data as { partnerId?: unknown }).partnerId ?? (data as { partner?: unknown }).partner;
            return { role, partnerId: typeof partnerId === "string" && partnerId.trim().length > 0 ? partnerId : null };
        } catch {
        }
    }
    return null;
}

function whiteLabelPartnerFromPath(pathname: string): string | null {
    const m = pathname.match(/^\/white-label\/([^/]+)(?:\/|$)/);
    if (!m) return null;
    const seg = (m[1] ?? "").trim();
    if (!seg) return null;
    if (seg.toLowerCase() === "dashboard") return null;
    return seg;
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

export async function middleware(req: NextRequest) {
    const { pathname, search } = req.nextUrl;

    const inProd = process.env.NODE_ENV === "production";
    const https = isHttps(req);

    if (req.headers.get(INTERNAL_BYPASS_HEADER) === "1") {
        return NextResponse.next();
    }

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

    const cspParts = [
        "default-src 'self'",
        `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        "connect-src " + connectSrc,
        // media-src needs the backend origin so <audio src> can load the
        // ElevenLabs preview MP3s from api.talkleeai.com. Reuse the same
        // origin list as connect-src so anything we can fetch we can also
        // load as media. Without this the browser silently blocks audio
        // playback with a CSP violation, no network request fires, and
        // the UI sees no error.
        "media-src " + connectSrc,
        "worker-src 'self' blob:",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        ...(https ? ["upgrade-insecure-requests", "block-all-mixed-content"] : []),
    ];
    const csp = cspParts.join("; ");

    const requestHeaders = new Headers(req.headers);
    requestHeaders.set("content-security-policy", csp);
    requestHeaders.set("x-nonce", nonce);

    // Auth signal: any of the cookies that indicate the user is logged
    // in. Phase B adds `talky_at` (the new httpOnly access cookie) as
    // the canonical signal alongside the legacy non-httpOnly mirror
    // cookie and `talky_sid` legacy server-side session cookie.
    const legacyToken = readCookieFromHeader(req, authTokenCookieName());
    const accessCookie = readCookieFromHeader(req, "talky_at");
    const legacySession = readCookieFromHeader(req, "talky_sid");
    const hasAuthSignal =
        (accessCookie && accessCookie.trim().length > 0) ||
        (legacyToken && legacyToken.trim().length > 0) ||
        (legacySession && legacySession.trim().length > 0);
    const cookieHeader = req.headers.get("cookie") ?? "";
    if (hasAuthSignal) {
        const shouldCheckRole =
            !isApiPath(pathname) &&
            !pathname.startsWith("/_next/") &&
            !pathname.startsWith("/favicon") &&
            !pathname.startsWith("/site.webmanifest");

        if (shouldCheckRole) {
            const ctx = await fetchUserContextFromBackend({ req, cookieHeader });
            const role = ctx?.role ?? null;
            const partnerId = ctx?.partnerId ?? null;

            if (role === WHITE_LABEL_ADMIN_ROLE) {
                const isAllowed =
                    isWhiteLabelPath(pathname) ||
                    isConnectorCallbackPath(pathname) ||
                    pathname === "/403" ||
                    pathname === WHITE_LABEL_DASHBOARD_PATH;

                if (!isAllowed) {
                    const url = req.nextUrl.clone();
                    url.pathname = WHITE_LABEL_DASHBOARD_PATH;
                    url.search = "";
                    const res = NextResponse.redirect(url);
                    setSecurityHeaders(res, { csp, inProd, https });
                    return res;
                }
            } else if (role && role !== WHITE_LABEL_ADMIN_ROLE) {
                if (isWhiteLabelAdminPath(pathname)) {
                    const url = req.nextUrl.clone();
                    url.pathname = "/403";
                    url.search = "";
                    const res = NextResponse.redirect(url);
                    setSecurityHeaders(res, { csp, inProd, https });
                    return res;
                }

                if (role === PARTNER_ADMIN_ROLE && partnerId && isWhiteLabelPath(pathname)) {
                    if (pathname === "/white-label" || pathname === "/white-label/") {
                        const url = req.nextUrl.clone();
                        url.pathname = `/white-label/${encodeURIComponent(partnerId)}/dashboard`;
                        url.search = "";
                        const res = NextResponse.redirect(url);
                        setSecurityHeaders(res, { csp, inProd, https });
                        return res;
                    }

                    const wlPartner = whiteLabelPartnerFromPath(pathname);
                    if (wlPartner && wlPartner.toLowerCase() !== partnerId.toLowerCase()) {
                        const url = req.nextUrl.clone();
                        url.pathname = "/403";
                        url.search = "";
                        const res = NextResponse.redirect(url);
                        setSecurityHeaders(res, { csp, inProd, https });
                        return res;
                    }
                }
            } else {
                if (isWhiteLabelAdminPath(pathname) || isAdminOrInfrastructurePath(pathname)) {
                    const url = req.nextUrl.clone();
                    url.pathname = "/403";
                    url.search = "";
                    const res = NextResponse.redirect(url);
                    setSecurityHeaders(res, { csp, inProd, https });
                    return res;
                }
            }
        }

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

    // Fail-closed for protected paths.
    //
    // The earlier 2026-05-21 hotfix made this branch fail-open because
    // Phase 7 had broken the middleware's view of the session (the
    // canonical talky_at cookie was scoped host-only to
    // api.talkleeai.com and never reached this edge function).
    //
    // The systemic fix shipped the same day: backend now issues
    // talky_at with `Domain=talkleeai.com` (AUTH_COOKIE_DOMAIN env
    // var). The cookie is now visible to every subdomain that shares
    // the registrable domain — talkleeai.com (this middleware),
    // api.talkleeai.com, admin.talkleeai.com (when added), etc. The
    // hasAuthSignal check above now genuinely reflects "this client
    // has an active session", so we can safely redirect anonymous
    // visitors away from /dashboard without bouncing real users.
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
