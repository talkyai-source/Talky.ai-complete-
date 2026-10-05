import { getBrowserAuthToken, setBrowserAuthToken } from "@/lib/auth-token";

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
    // When true, a 401 on this request does NOT clear the stored token or fire
    // the global session-expired redirect — it just throws so the caller can
    // handle it locally. Use for AUTH PROBES (/auth/me bootstrap) and OPTIONAL
    // background calls (e.g. the assistant model picker): an anonymous visitor's
    // probe 401 must not bounce them to /login, and an optional widget's 401
    // must not tear down a live session. Real authed calls omit this so genuine
    // expiry still redirects.
    suppressAuthRedirect?: boolean;
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
let _externalTokenWriter: ((token: string | null) => void) | null = null;
export type RequestIdentity = { userId: string; tenantId?: string };
let _requestIdentity: RequestIdentity | null = null;
let _identityEpoch = 0;
const PUBLIC_AUTH_PATHS = new Set([
    "/auth/login", "/auth/verify-otp", "/auth/signup/start", "/auth/signup/verify-code",
    "/auth/signup/complete", "/auth/register", "/auth/forgot-password", "/auth/reset-password",
    "/auth/passkey-check", "/auth/passkeys/login/begin", "/auth/passkeys/login/complete", "/auth/mfa/verify",
]);
function managesSession(path: string) {
    return PUBLIC_AUTH_PATHS.has(path) || path === "/auth/logout" || path === "/auth/refresh";
}

export function setRequestIdentity(identity: RequestIdentity | null) {
    if (JSON.stringify(identity) !== JSON.stringify(_requestIdentity)) _identityEpoch += 1;
    _requestIdentity = identity ? { ...identity } : null;
}

export function invalidateRequestIdentity() {
    _identityEpoch += 1;
    _requestIdentity = null;
}

export function setTokenProvider(fn: (() => string | null) | null, writer?: (token: string | null) => void) {
    _externalTokenProvider = fn;
    _externalTokenWriter = fn ? writer ?? null : null;
    if (!fn) invalidateRequestIdentity();
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

/**
 * AH-Phase-E: clear the grace window immediately. Called by
 * AuthContext.logout so a user who logs in and out within 15s doesn't
 * leave a lingering suppression window — a subsequent 401 in the next
 * remnant seconds would otherwise be silently swallowed instead of
 * bouncing the now-anonymous user.
 */
export function clearFreshLoginGrace() {
    _freshLoginUntil = 0;
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
    return {
        get: () => {
            // AuthContext owns the current identity, including an explicit
            // absence. Never revive a stale stored token after local logout.
            if (_externalTokenProvider) {
                try {
                    return _externalTokenProvider();
                } catch {
                    return null;
                }
            }
            if (typeof window === "undefined") return mem;
            return getBrowserAuthToken();
        },
        set: (token) => {
            if (_externalTokenWriter) {
                _externalTokenWriter(token);
                return;
            }
            mem = token;
            setBrowserAuthToken(token);
        },
    };
}

// Shared, single-flight refresh-on-401. The cookie-auth backend
// (Phase A) rotates the short-lived `talky_at` access cookie via
// `POST /api/v1/auth/refresh` using the `talky_rt` refresh cookie. The
// HTTP client retries any first-time 401 once, after a successful
// refresh. Concurrent 401s share a single in-flight refresh promise so
// a thundering herd doesn't trigger N rotations.
type RefreshResult = { ok: boolean; status?: number; accessToken?: string; userId?: string; tenantId?: string };
const _refreshInFlight = new Map<string, Promise<RefreshResult>>();

async function tryRefresh(refreshUrl: string, key: string, headers: Record<string, string>): Promise<RefreshResult> {
    const existing = _refreshInFlight.get(key);
    if (existing) return existing;
    const promise = (async () => {
        try {
            const res = await fetch(refreshUrl, {
                method: "POST",
                credentials: "include",
                headers,
            });
            const body = res.ok && res.headers.get("content-type")?.includes("application/json")
                ? await res.json() as Record<string, unknown> : undefined;
            return { ok: res.ok, status: res.status,
                accessToken: typeof body?.access_token === "string" ? body.access_token : undefined,
                userId: typeof body?.user_id === "string" ? body.user_id : undefined,
                tenantId: typeof body?.tenant_id === "string" ? body.tenant_id : undefined };
        } catch {
            return { ok: false };
        } finally {
            setTimeout(() => { _refreshInFlight.delete(key); }, 0);
        }
    })();
    _refreshInFlight.set(key, promise);
    return promise;
}

/** Test-only: reset module-level refresh state between tests. */
export function __resetRefreshStateForTests() {
    _refreshInFlight.clear();
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

    function captureScope() {
        return { token: getToken(), epoch: _identityEpoch, identity: _requestIdentity ? { ..._requestIdentity } : null };
    }
    type RequestScope = ReturnType<typeof captureScope>;
    function assertScope(scope: RequestScope, opts: HttpRequestOptions) {
        if (_identityEpoch !== scope.epoch || getToken() !== scope.token) {
            throw new ApiClientError({ code: "identity_changed",
                message: "Your sign-in changed while this request was pending. Check saved results before submitting it again.",
                url: buildUrl(baseUrl, opts.path, opts.query ?? opts.params), method: opts.method ?? "GET" });
        }
    }
    function identityHeaders(scope: RequestScope) {
        const headers: Record<string, string> = {};
        if (scope.identity) {
            headers["X-Talky-Expected-User"] = scope.identity.userId;
            headers["X-Talky-Expected-Tenant"] = scope.identity.tenantId ?? "";
        }
        return headers;
    }
    async function refreshForScope(scope: RequestScope, opts: HttpRequestOptions) {
        assertScope(scope, opts);
        const key = JSON.stringify([refreshUrl, scope.epoch, scope.identity, scope.token]);
        const result = await tryRefresh(refreshUrl, key, identityHeaders(scope));
        // A concurrent request in this same scope can adopt the same refresh.
        if (result.accessToken && getToken() === result.accessToken && _identityEpoch === scope.epoch) scope.token = result.accessToken;
        assertScope(scope, opts);
        if (result.status === 409) {
            throw new ApiClientError({ code: "identity_changed", message: "Your sign-in changed. Refresh the page before continuing.",
                url: refreshUrl, method: "POST" });
        }
        if (result.ok && result.accessToken) {
            if (scope.identity && (result.userId !== scope.identity.userId
                || (result.tenantId ?? "") !== (scope.identity.tenantId ?? ""))) {
                throw new ApiClientError({ code: "identity_changed", message: "Your sign-in changed. Refresh the page before continuing.",
                    url: refreshUrl, method: "POST" });
            }
            setToken(result.accessToken);
            scope.token = result.accessToken;
        }
        return result.ok;
    }

    function guardResponseBody(res: Response, scope: RequestScope, opts: HttpRequestOptions): Response {
        // Existing binary/import callers read the body after requestRaw resolves.
        // Their identity must still match when that asynchronous read finishes.
        const guarded = <T>(read: () => Promise<T>) => async () => {
            assertScope(scope, opts);
            const value = await read();
            assertScope(scope, opts);
            return value;
        };
        res.blob = guarded(res.blob.bind(res));
        res.json = guarded(res.json.bind(res));
        res.text = guarded(res.text.bind(res));
        res.arrayBuffer = guarded(res.arrayBuffer.bind(res));
        res.formData = guarded(res.formData.bind(res));
        const clone = res.clone.bind(res);
        res.clone = () => { assertScope(scope, opts); return guardResponseBody(clone(), scope, opts); };
        return res;
    }

    async function raw<TBody = unknown>(opts: HttpRequestOptions<TBody>, scope = captureScope()) {
        const method = opts.method ?? "GET";
        const queryParams = opts.query ?? opts.params; // Support both query and params
        const url = buildUrl(baseUrl, opts.path, queryParams);
        const bootstrap = method === "GET" && (opts.path === "/auth/me" || opts.path === "/me" || opts.path === "/health");
        if (_externalTokenProvider && !scope.identity && !managesSession(opts.path) && !bootstrap) {
            throw new ApiClientError({ code: "identity_unverified", message: "Please wait while your sign-in is checked, then try again.", url, method });
        }
        const headers: Record<string, string> = {};
        for (const [k, v] of Object.entries(opts.headers ?? {})) {
            if (v === undefined) continue;
            headers[k] = v;
        }

        Object.assign(headers, identityHeaders(scope));
        const token = scope.token;
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
            assertScope(scope, opts);
            let res = await fetch(cur.url, cur.init);
            for (const interceptor of responseInterceptors) {
                assertScope(scope, opts);
                res = await interceptor(res);
            }
            assertScope(scope, opts);

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
        const scope = captureScope();
        const method = opts.method ?? "GET";
        const queryParams = opts.query ?? opts.params; // Support both query and params
        const url = buildUrl(baseUrl, opts.path, queryParams);

        // Don't try to refresh the refresh endpoint itself — that would
        // recurse forever on a genuinely expired refresh token.
        const isRefreshCall = managesSession(opts.path);

        let res: Response;
        try {
            res = await raw(opts, scope);
            if (res.status === 401 && !isRefreshCall) {
                const refreshed = await refreshForScope(scope, opts);
                if (refreshed) {
                    res = await raw(opts, scope);
                }
            }
        } catch (err) {
            if (err instanceof ApiClientError) throw err;
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
            assertScope(scope, opts);

            // Canonical envelope from backend: { error: { code, message, details, request_id } }.
            // Falls back to the legacy { detail: string|dict } shape (FastAPI default)
            // for any endpoint that still raises HTTPException directly.
            const envelope =
                body && typeof body === "object" && "error" in (body as Record<string, unknown>)
                    ? ((body as { error?: unknown }).error as { code?: unknown; message?: unknown; details?: unknown } | undefined)
                    : undefined;
            const envelopeCode = typeof envelope?.code === "string" ? envelope.code : undefined;
            const envelopeMessage = typeof envelope?.message === "string" ? envelope.message : undefined;
            const envelopeDetails = envelope?.details;

            const legacyDetail =
                body && typeof body === "object" && "detail" in (body as Record<string, unknown>)
                    ? (body as { detail?: unknown }).detail
                    : undefined;
            const legacyMessage = typeof legacyDetail === "string" ? legacyDetail : undefined;
            const legacyRecord =
                legacyDetail && typeof legacyDetail === "object"
                    ? legacyDetail as Record<string, unknown>
                    : undefined;
            const legacyCodeValue = legacyRecord?.code ?? legacyRecord?.error;
            const legacyCode = typeof legacyCodeValue === "string" ? legacyCodeValue : undefined;
            const legacyObjectMessage = typeof legacyRecord?.message === "string" ? legacyRecord.message : undefined;
            const legacyObjectDetails = legacyRecord && "details" in legacyRecord
                ? legacyRecord.details
                : legacyDetail;

            const defaultCode =
                res.status === 401
                    ? "unauthorized"
                    : res.status === 403
                      ? "forbidden"
                      : res.status === 429
                        ? "rate_limited"
                        : res.status >= 500
                          ? "server_error"
                          : "http_error";

            const code = envelopeCode ?? legacyCode ?? defaultCode;
            const message = envelopeMessage ?? legacyObjectMessage ?? legacyMessage ?? defaultMessageForStatus(res.status);
            const detailsForError = envelopeDetails ?? legacyObjectDetails ?? body;

            // Single source of truth for token expiry — clear the
            // stored token, then fire the global session-expired
            // handler so EVERY caller path (react-query, manual
            // try/catch, fire-and-forget services) gets the same
            // redirect-to-login behaviour. The handler is
            // idempotent — parallel requests racing on 401 trigger
            // exactly one redirect.
            if (res.status === 401 && !opts.suppressAuthRedirect && !managesSession(opts.path)) {
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
                details: detailsForError,
                retryAfterMs,
                requestId,
                url,
                method,
            });
        }

        const ct = res.headers.get("content-type") ?? "";
        if (ct.includes("application/json")) {
            const result = await res.json();
            assertScope(scope, opts);
            return result as TResponse;
        }
        const result = await res.text();
        assertScope(scope, opts);
        return result as unknown as TResponse;
    }

    /**
     * Like {@link request} but returns the raw {@link Response} without
     * parsing — for binary endpoints (audio streams, file downloads) and
     * multipart responses the JSON-oriented `request` can't handle.
     *
     * Crucially it reuses the SAME auth (cookie + optional bearer) AND the
     * single-flight refresh-on-401 retry. Before this, the recordings audio
     * stream and CSV upload used bare `fetch()` calls that did NOT refresh,
     * so once the short-lived `talky_at` cookie rotated (~15 min) they 401'd
     * and surfaced as "Failed to load audio" / failed upload — even though
     * the cookie-auth backend was healthy. Routing them through here makes
     * them survive a rotated cookie exactly like every JSON call does.
     *
     * Throws {@link ApiClientError} on a non-OK response.
     */
    async function requestRaw<TBody = unknown>(opts: HttpRequestOptions<TBody>): Promise<Response> {
        const scope = captureScope();
        const method = opts.method ?? "GET";
        const url = buildUrl(baseUrl, opts.path, opts.query ?? opts.params);
        const isRefreshCall = managesSession(opts.path);

        let res: Response;
        try {
            res = await raw(opts, scope);
            if (res.status === 401 && !isRefreshCall) {
                const refreshed = await refreshForScope(scope, opts);
                if (refreshed) {
                    res = await raw(opts, scope);
                }
            }
        } catch (err) {
            if (err instanceof ApiClientError) throw err;
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
            const requestId = res.headers.get("x-request-id") ?? res.headers.get("x-correlation-id") ?? undefined;
            const defaultCode =
                res.status === 401
                    ? "unauthorized"
                    : res.status === 403
                      ? "forbidden"
                      : res.status === 429
                        ? "rate_limited"
                        : res.status >= 500
                          ? "server_error"
                          : "http_error";
            // Safe to consume the body here — the response is an error, so the
            // caller won't read it as binary. Surface the backend's detail
            // (e.g. CSV validation errors) instead of a generic status line.
            const body = await readBody(res);
            assertScope(scope, opts);
            if (res.status === 401 && !opts.suppressAuthRedirect && !managesSession(opts.path)) {
                if (!isWithinFreshLoginGrace()) {
                    try { setToken(null); } catch { /* Preserve the original failure. */ }
                }
                fireSessionExpired();
            }
            const envelope =
                body && typeof body === "object" && "error" in (body as Record<string, unknown>)
                    ? ((body as { error?: unknown }).error as { code?: unknown; message?: unknown; details?: unknown } | undefined)
                    : undefined;
            const envelopeCode = typeof envelope?.code === "string" ? envelope.code : undefined;
            const envelopeMessage = typeof envelope?.message === "string" ? envelope.message : undefined;
            const envelopeDetails = envelope?.details;
            const legacyDetail =
                body && typeof body === "object" && "detail" in (body as Record<string, unknown>)
                    ? (body as { detail?: unknown }).detail
                    : undefined;
            const legacyMessage = typeof legacyDetail === "string" ? legacyDetail : undefined;
            const legacyRecord =
                legacyDetail && typeof legacyDetail === "object"
                    ? legacyDetail as Record<string, unknown>
                    : undefined;
            const legacyCodeValue = legacyRecord?.code ?? legacyRecord?.error;
            const legacyCode = typeof legacyCodeValue === "string" ? legacyCodeValue : undefined;
            const legacyObjectMessage = typeof legacyRecord?.message === "string" ? legacyRecord.message : undefined;
            const legacyObjectDetails = legacyRecord && "details" in legacyRecord
                ? legacyRecord.details
                : legacyDetail;
            throw new ApiClientError({
                status: res.status,
                code: envelopeCode ?? legacyCode ?? defaultCode,
                message: envelopeMessage ?? legacyObjectMessage ?? legacyMessage ?? defaultMessageForStatus(res.status),
                details: envelopeDetails ?? legacyObjectDetails ?? body,
                url,
                method,
                requestId,
            });
        }

        return guardResponseBody(res, scope, opts);
    }

    return {
        request,
        raw,
        requestRaw,
        setToken,
        getToken,
    };
}
