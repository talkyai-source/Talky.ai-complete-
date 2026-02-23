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

function defaultTokenStorage(): TokenStorage {
    let mem: string | null = null;
    const key = "talklee.auth.token";
    return {
        get: () => {
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

export function createHttpClient(config: HttpClientConfig) {
    const baseUrl = normalizeBaseUrl(config.baseUrl);
    const storage = defaultTokenStorage();
    const getToken = config.getToken ?? storage.get;
    const setToken = config.setToken ?? storage.set;

    const requestInterceptors = config.requestInterceptors ?? [];
    const responseInterceptors = config.responseInterceptors ?? [];

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

        let res: Response;
        try {
            res = await raw(opts);
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
