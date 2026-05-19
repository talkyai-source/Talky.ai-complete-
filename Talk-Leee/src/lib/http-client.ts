export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export type UnifiedApiError = {
    status?: number;
    code: string;
    message: string;
    details?: unknown;
    retryAfterMs?: number;
    requestId?: string;
};

export class ApiClientError extends Error {
    readonly status?: number;
    readonly code: string;
    readonly details?: unknown;
    readonly retryAfterMs?: number;
    readonly requestId?: string;
    readonly url: string;
    readonly method: HttpMethod;

    constructor(init: UnifiedApiError & { url: string; method: HttpMethod }) {
        super(init.message);
        this.name = "ApiClientError";
        this.status = init.status;
        this.code = init.code;
        this.details = init.details;
        this.retryAfterMs = init.retryAfterMs;
        this.requestId = init.requestId;
        this.url = init.url;
        this.method = init.method;
    }
}

export function isApiClientError(err: unknown): err is ApiClientError {
    return err instanceof Error && err.name === "ApiClientError";
}

type QueryValue = string | number | boolean | null | undefined;

export type HttpRequestOptions<TBody = unknown> = {
    path: string;
    method?: HttpMethod;
    query?: Record<string, QueryValue>;
    params?: Record<string, QueryValue>; // Alias for query
    headers?: Record<string, string | undefined>;
    body?: TBody;
    timeoutMs?: number;
    signal?: AbortSignal;
};

type TokenStorage = {
    get: () => string | null;
    set: (token: string | null) => void;
};

export type HttpClientConfig = {
    baseUrl: string;
    getToken?: () => string | null;
    setToken?: (token: string | null) => void;
    requestInterceptors?: Array<(init: { url: string; init: RequestInit }) => Promise<{ url: string; init: RequestInit }> | { url: string; init: RequestInit }>;
    responseInterceptors?: Array<(res: Response) => Promise<Response> | Response>;
};

function normalizeBaseUrl(baseUrl: string) {
    return baseUrl.endsWith("/") ? baseUrl.slice(0, -1) : baseUrl;
}

function buildUrl(baseUrl: string, path: string, query?: Record<string, QueryValue>) {
    const cleanPath = path.startsWith("/") ? path : `/${path}`;
    const url = new URL(`${normalizeBaseUrl(baseUrl)}${cleanPath}`);
    if (query) {
        for (const [k, v] of Object.entries(query)) {
            if (v === undefined || v === null) continue;
            url.searchParams.set(k, String(v));
        }
    }
    return url.toString();
}

function readRetryAfterMs(res: Response) {
    const raw = res.headers.get("retry-after");
    if (!raw) return undefined;
    const seconds = Number(raw);
    if (Number.isFinite(seconds)) return Math.max(0, seconds) * 1000;
    const dateMs = Date.parse(raw);
    if (Number.isFinite(dateMs)) return Math.max(0, dateMs - Date.now());
    return undefined;
}

async function readBody(res: Response) {
    const ct = res.headers.get("content-type") ?? "";
    if (ct.includes("application/json")) {
        try {
            return await res.json();
        } catch {
            return undefined;
        }
    }
    try {
        const text = await res.text();
        return text.length ? text : undefined;
    } catch {
        return undefined;
    }
}

function defaultMessageForStatus(status?: number) {
    if (status === 401) return "Unauthorized";
    if (status === 403) return "Forbidden";
    if (status === 429) return "Rate limited";
    if (status && status >= 500) return "Server error";
    return "Request failed";
}

// ──────────────────────────────────────────────────────────────────────────
// Session-expired handler (single source of truth for 401 redirects)
//
// Before this, every page that called the API directly via fetch / a service
// had its own try/catch with a per-page 401 check. Some pages redirected,
// others showed a red error message and stayed put — inconsistent, and a
// "still functional but expired" call was the result. The fix is to handle
// the redirect ONCE, at the http-client layer, so every consumer (react-
// query, direct fetch wrappers, manual try/catch) gets the same behaviour.
//
// The handler is module-level + idempotent. Multiple parallel requests
// hitting 401 simultaneously trigger exactly one redirect.
// ──────────────────────────────────────────────────────────────────────────

let _sessionExpiredHandler: (() => void) | null = null;
let _sessionExpiredFired = false;
let _freshLoginUntil = 0;

// ──────────────────────────────────────────────────────────────────────────
// Token provider — Phase 2 of the universal-auth-state refactor.
//
// Before this, every HttpClient instance defaulted to reading the Bearer
// token from `localStorage["talklee.auth.token"]` AT REQUEST TIME. That
// works but it means components and other readers also have to reach into
// localStorage themselves to "see" the current token — there's no
// reactive subscription, and a token rotation isn't visible to a
// component that did `useMemo(() => getBrowserAuthToken(), [])` at mount.
//
// With Phase 2's universal model, `AuthContext` becomes the single owner
// of the token state. It installs itself as the provider on mount via
// `setTokenProvider(() => authContextState.accessToken)`. The HTTP client
// then reads `_externalTokenProvider()` (when set) at request time, so
// any rotation in AuthContext is automatically picked up by every API
// call without anyone re-reading localStorage.
//
// We deliberately do NOT import AuthContext here to avoid a circular
// import (auth-context.tsx imports `api` which depends on this module).
// The deferred-injection pattern: AuthContext mounts → calls
// setTokenProvider in a useEffect → from that point on, every request
// reads the live token. The brief pre-mount window (between this module
// loading and AuthProvider mounting) falls back to the localStorage
// reader — which is what every request did before Phase 2 anyway, so
// the SSR + first-paint paths are unchanged.
// ──────────────────────────────────────────────────────────────────────────
let _externalTokenProvider: (() => string | null) | null = null;

export function setTokenProvider(fn: (() => string | null) | null) {
    _externalTokenProvider = fn;
}

// Grace window after a fresh login. Any 401 (including one from
// /auth/refresh failing) inside this window is treated as a transient
// race — we do NOT fire session-expired and do NOT clear the stored
// token. The user just got a valid login response; bouncing them back
// to /login because /auth/me 401'd a few hundred ms later is the bug
// users keep hitting on prod.
//
// Causes the grace window covers:
//   - Clock skew between client JWT iat and server's "not before"
//   - Browser cookie commit lagging the localStorage write
//   - Refresh token rotation racing with parallel /auth/me calls
//   - Stale cookies from a prior origin (vercel.app) still in the jar
// 15s window — same value Auth0 / Clerk use for the post-login race
// against cookie commit + JWT iat skew. 8s wasn't enough on slow networks.
const FRESH_LOGIN_GRACE_MS = 15000;

export function markFreshLogin() {
    _sessionExpiredFired = false;
    _freshLoginUntil = Date.now() + FRESH_LOGIN_GRACE_MS;
}

// Exported so the auth-context catches + dashboard-layout guard can
// consult it. Without this, a transient 401 from `api.getMe()` thrown
// to a downstream `.catch(() => setUser(null))` re-triggers the bounce
// even though we suppressed `fireSessionExpired()` here.
export function isWithinFreshLoginGrace(): boolean {
    return Date.now() < _freshLoginUntil;
}

export function setSessionExpiredHandler(fn: (() => void) | null) {
    _sessionExpiredHandler = fn;
    // Reset the fired latch so a logout → login round-trip can re-arm.
    _sessionExpiredFired = false;
}

/**
 * Reset the "already fired" latch. Call after a successful login so the
 * next 401 fires the redirect again.
 */
export function resetSessionExpiredLatch() {
    _sessionExpiredFired = false;
}

function fireSessionExpired() {
    // Don't bounce the user back to /login if they JUST finished a
    // successful login round-trip. Within FRESH_LOGIN_GRACE_MS the
    // backend session is still settling (cookie commits, JWT iat skew,
    // refresh-token rotation race) — any 401 here is almost certainly
    // transient and we should let the request fail soft instead of
    // tearing down auth state.
    if (isWithinFreshLoginGrace()) {
        if (typeof console !== "undefined" && process.env.NODE_ENV !== "production") {
            console.debug("[auth] session-expired swallowed inside fresh-login grace window");
        }
        return;
    }
    if (_sessionExpiredFired) return;
    _sessionExpiredFired = true;
    if (!_sessionExpiredHandler) return;
    try {
        _sessionExpiredHandler();
    } catch {
        // Handler errors must never break the request pipeline.
    }
}

function defaultTokenStorage(): TokenStorage {
    let mem: string | null = null;
    const key = "talklee.auth.token";
    return {
        get: () => {
            // Prefer the externally-installed provider when AuthContext has
            // mounted (Phase 2 universal-auth-state). Falls back to direct
            // localStorage for SSR + the pre-mount window.
            if (_externalTokenProvider) {
                try {
                    const v = _externalTokenProvider();
                    if (v !== undefined) return v;
                } catch {
                    // Provider threw — fall through to localStorage.
                }
            }
            if (typeof window === "undefined") return mem;
            try {
                return window.localStorage.getItem(key);
            } catch {
                return mem;
            }
        },
        set: (token) => {
            mem = token;
            if (typeof window === "undefined") return;
            try {
                if (token) window.localStorage.setItem(key, token);
                else window.localStorage.removeItem(key);
            } catch {
            }
        },
    };
}

// Shared, single-flight refresh-on-401. The cookie-auth backend
// (Phase A) rotates the short-lived `talky_at` access cookie via
// `POST /api/v1/auth/refresh` using the `talky_rt` refresh cookie. The
// HTTP client retries any first-time 401 once, after a successful
// refresh. Concurrent 401s share a single in-flight refresh promise so
// a thundering herd doesn't trigger N rotations.
let _refreshInFlight: Promise<boolean> | null = null;

async function tryRefresh(refreshUrl: string): Promise<boolean> {
    if (_refreshInFlight) return _refreshInFlight;
    _refreshInFlight = (async () => {
        try {
            const res = await fetch(refreshUrl, {
                method: "POST",
                credentials: "include",
            });
            return res.ok;
        } catch {
            return false;
        } finally {
            setTimeout(() => { _refreshInFlight = null; }, 0);
        }
    })();
    return _refreshInFlight;
}

/** Test-only: reset module-level refresh state between tests. */
export function __resetRefreshStateForTests() {
    _refreshInFlight = null;
}

export function createHttpClient(config: HttpClientConfig) {
    const baseUrl = normalizeBaseUrl(config.baseUrl);
    const storage = defaultTokenStorage();
    const getToken = config.getToken ?? storage.get;
    const setToken = config.setToken ?? storage.set;

    const requestInterceptors = config.requestInterceptors ?? [];
    const responseInterceptors = config.responseInterceptors ?? [];

    // Refresh endpoint lives at the same baseUrl; the backend mounts it
    // at /auth/refresh (after the /api/v1 prefix that baseUrl already
    // includes for FastAPI clients, or under /api/v1/auth/refresh when
    // routed through the Next.js proxy).
    const refreshUrl = `${baseUrl}/auth/refresh`;

    async function raw<TBody = unknown>(opts: HttpRequestOptions<TBody>) {
        const method = opts.method ?? "GET";
        const queryParams = opts.query ?? opts.params; // Support both query and params
        const url = buildUrl(baseUrl, opts.path, queryParams);
        const headers: Record<string, string> = {};
        for (const [k, v] of Object.entries(opts.headers ?? {})) {
            if (v === undefined) continue;
            headers[k] = v;
        }

        const token = getToken();
        if (token && !headers.Authorization && !headers.authorization) {
            headers.Authorization = `Bearer ${token}`;
        }

        const rawBody = opts.body as unknown;
        let body: BodyInit | undefined;
        let isJson = false;
        if (rawBody !== undefined) {
            if (
                typeof rawBody === "string" ||
                rawBody instanceof FormData ||
                rawBody instanceof URLSearchParams ||
                rawBody instanceof Blob ||
                rawBody instanceof ArrayBuffer
            ) {
                body = rawBody;
            } else {
                body = JSON.stringify(rawBody);
                isJson = true;
            }
        }
        if (isJson && body && !headers["Content-Type"] && !headers["content-type"]) {
            headers["Content-Type"] = "application/json";
        }

        const controller = new AbortController();
        const externalSignal = opts.signal;
        const onAbort = () => controller.abort(externalSignal?.reason);
        if (externalSignal) {
            if (externalSignal.aborted) onAbort();
            else externalSignal.addEventListener("abort", onAbort, { once: true });
        }

        const timeoutMs = opts.timeoutMs;
        const timeoutId =
            typeof timeoutMs === "number" && timeoutMs > 0
                ? setTimeout(() => controller.abort(new Error("Timeout")), timeoutMs)
                : undefined;

        const start = typeof performance !== "undefined" ? performance.now() : Date.now();
        const initBase: RequestInit = {
            method,
            headers,
            body,
            credentials: "include",
            signal: controller.signal,
        };

        let cur = { url, init: initBase };
        for (const interceptor of requestInterceptors) {
            cur = await interceptor(cur);
        }

        try {
            let res = await fetch(cur.url, cur.init);
            for (const interceptor of responseInterceptors) {
                res = await interceptor(res);
            }

            if (process.env.NODE_ENV === "development") {
                const end = typeof performance !== "undefined" ? performance.now() : Date.now();
                const ms = Math.round(end - start);
                const safeHeaders = { ...headers };
                if (safeHeaders.Authorization) safeHeaders.Authorization = "Bearer <redacted>";
                console.debug(`[api] ${method} ${url} -> ${res.status} (${ms}ms)`, safeHeaders);
            }

            return res;
        } finally {
            if (timeoutId !== undefined) clearTimeout(timeoutId);
            if (externalSignal) externalSignal.removeEventListener("abort", onAbort);
        }
    }

    async function request<TResponse = unknown, TBody = unknown>(opts: HttpRequestOptions<TBody>): Promise<TResponse> {
        const method = opts.method ?? "GET";
        const queryParams = opts.query ?? opts.params; // Support both query and params
        const url = buildUrl(baseUrl, opts.path, queryParams);

        // Don't try to refresh the refresh endpoint itself — that would
        // recurse forever on a genuinely expired refresh token.
        const isRefreshCall = opts.path === "/auth/refresh" || opts.path.endsWith("/auth/refresh");

        let res: Response;
        try {
            res = await raw(opts);
            if (res.status === 401 && !isRefreshCall) {
                const refreshed = await tryRefresh(refreshUrl);
                if (refreshed) {
                    res = await raw(opts);
                }
            }
        } catch (err) {
            if (err instanceof DOMException && err.name === "AbortError") {
                throw new ApiClientError({ code: "aborted", message: "Request aborted", url, method });
            }
            if (err instanceof Error && err.message === "Timeout") {
                throw new ApiClientError({ code: "timeout", message: "Request timed out", url, method });
            }
            throw new ApiClientError({
                code: "network_error",
                message: err instanceof Error ? err.message : "Network error",
                url,
                method,
                details: err,
            });
        }

        if (!res.ok) {
            const retryAfterMs = res.status === 429 ? readRetryAfterMs(res) : undefined;
            const requestId = res.headers.get("x-request-id") ?? res.headers.get("x-correlation-id") ?? undefined;
            const body = await readBody(res);
            const detail =
                body && typeof body === "object" && "detail" in (body as Record<string, unknown>) ? (body as { detail?: unknown }).detail : undefined;
            const message = typeof detail === "string" ? detail : defaultMessageForStatus(res.status);
            const code =
                res.status === 401
                    ? "unauthorized"
                    : res.status === 403
                      ? "forbidden"
                      : res.status === 429
                        ? "rate_limited"
                        : res.status >= 500
                          ? "server_error"
                          : "http_error";

            // Single source of truth for token expiry — clear the
            // stored token, then fire the global session-expired
            // handler so EVERY caller path (react-query, manual
            // try/catch, fire-and-forget services) gets the same
            // redirect-to-login behaviour. The handler is
            // idempotent — parallel requests racing on 401 trigger
            // exactly one redirect.
            if (res.status === 401) {
                // Inside the fresh-login grace window, keep the bearer
                // token in storage — wiping it would force the next
                // call to use cookie-only auth even though localStorage
                // still has the valid JWT from /auth/login.
                if (!isWithinFreshLoginGrace()) {
                    try {
                        setToken(null);
                    } catch {
                        // ignore — clearing storage must not derail the throw
                    }
                }
                fireSessionExpired();
            }

            throw new ApiClientError({
                status: res.status,
                code,
                message,
                details: body,
                retryAfterMs,
                requestId,
                url,
                method,
            });
        }

        const ct = res.headers.get("content-type") ?? "";
        if (ct.includes("application/json")) {
            return (await res.json()) as TResponse;
        }
        return (await res.text()) as unknown as TResponse;
    }

    return {
        request,
        raw,
        setToken,
        getToken,
    };
}
