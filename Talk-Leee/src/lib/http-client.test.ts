import { test } from "node:test";
import assert from "node:assert/strict";
import { createHttpClient, ApiClientError, __resetRefreshStateForTests } from "@/lib/http-client";

test("http client injects Authorization header when token present", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    const prevFetch = (globalThis as unknown as { fetch?: unknown }).fetch;
    (globalThis as unknown as { fetch: unknown }).fetch = (async (url: string, init?: RequestInit) => {
        calls.push({ url, init });
        return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "content-type": "application/json" } });
    }) as unknown as typeof fetch;

    const client = createHttpClient({
        baseUrl: "http://example.test",
        getToken: () => "token-123",
        setToken: () => {},
    });

    await client.request({ path: "/x" });
    const hdrs = calls[0]?.init?.headers as Record<string, string> | undefined;
    assert.equal(hdrs?.Authorization, "Bearer token-123");

    (globalThis as unknown as { fetch?: unknown }).fetch = prevFetch;
});

test("http client refreshes on 401 then retries the original request", async () => {
    __resetRefreshStateForTests();
    const calls: Array<{ url: string }> = [];
    const prevFetch = (globalThis as unknown as { fetch?: unknown }).fetch;
    (globalThis as unknown as { fetch: unknown }).fetch = (async (url: string) => {
        calls.push({ url });
        if (url.endsWith("/auth/refresh")) {
            return new Response(null, { status: 204 });
        }
        const dataCalls = calls.filter((c) => !c.url.endsWith("/auth/refresh")).length;
        if (dataCalls === 1) {
            return new Response(JSON.stringify({ detail: "expired" }), {
                status: 401,
                headers: { "content-type": "application/json" },
            });
        }
        return new Response(JSON.stringify({ ok: true }), {
            status: 200,
            headers: { "content-type": "application/json" },
        });
    }) as unknown as typeof fetch;

    const client = createHttpClient({ baseUrl: "http://example.test" });
    const result = await client.request<{ ok: boolean }>({ path: "/data" });
    assert.equal(result.ok, true);
    assert.equal(calls.length, 3);
    assert.ok(calls[0]!.url.endsWith("/data"));
    assert.ok(calls[1]!.url.endsWith("/auth/refresh"));
    assert.ok(calls[2]!.url.endsWith("/data"));

    (globalThis as unknown as { fetch?: unknown }).fetch = prevFetch;
});

test("http client surfaces 401 when refresh also fails", async () => {
    __resetRefreshStateForTests();
    const calls: Array<{ url: string }> = [];
    const prevFetch = (globalThis as unknown as { fetch?: unknown }).fetch;
    (globalThis as unknown as { fetch: unknown }).fetch = (async (url: string) => {
        calls.push({ url });
        if (url.endsWith("/auth/refresh")) {
            return new Response(JSON.stringify({ detail: "no refresh" }), {
                status: 401,
                headers: { "content-type": "application/json" },
            });
        }
        return new Response(JSON.stringify({ detail: "expired" }), {
            status: 401,
            headers: { "content-type": "application/json" },
        });
    }) as unknown as typeof fetch;

    const client = createHttpClient({ baseUrl: "http://example.test" });
    let caught: unknown;
    try {
        await client.request({ path: "/data" });
    } catch (e) {
        caught = e;
    }
    assert.ok(caught instanceof ApiClientError);
    assert.equal((caught as ApiClientError).status, 401);
    // refresh was attempted: data → refresh → no retry (refresh failed)
    assert.equal(calls.length, 2);
    assert.ok(calls[1]!.url.endsWith("/auth/refresh"));

    (globalThis as unknown as { fetch?: unknown }).fetch = prevFetch;
});

test("http client maps 429 response to rate_limited with retryAfterMs", async () => {
    const prevFetch = (globalThis as unknown as { fetch?: unknown }).fetch;
    (globalThis as unknown as { fetch: unknown }).fetch = (async () => {
        return new Response(JSON.stringify({ detail: "Too many requests" }), {
            status: 429,
            headers: { "content-type": "application/json", "retry-after": "2" },
        });
    }) as unknown as typeof fetch;

    const client = createHttpClient({ baseUrl: "http://example.test" });
    let caught: unknown;
    try {
        await client.request({ path: "/x" });
    } catch (e) {
        caught = e;
    }

    assert.ok(caught instanceof ApiClientError);
    const err = caught as ApiClientError;
    assert.equal(err.code, "rate_limited");
    assert.equal(err.status, 429);
    assert.ok(typeof err.retryAfterMs === "number" && err.retryAfterMs >= 2000);

    (globalThis as unknown as { fetch?: unknown }).fetch = prevFetch;
});

