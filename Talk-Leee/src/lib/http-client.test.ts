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

// Regression: the recordings audio stream + CSV upload are binary/multipart
// and go through requestRaw. They MUST get the same refresh-on-401 retry as
// JSON calls — otherwise a rotated talky_at cookie surfaces as
// "Failed to load audio" even while the backend is healthy.
test("requestRaw refreshes on 401 then retries and returns the raw binary Response", async () => {
    __resetRefreshStateForTests();
    const calls: Array<{ url: string }> = [];
    const audio = new Uint8Array([0x52, 0x49, 0x46, 0x46]); // "RIFF"
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
        return new Response(audio, { status: 200, headers: { "content-type": "audio/wav" } });
    }) as unknown as typeof fetch;

    const client = createHttpClient({ baseUrl: "http://example.test" });
    const res = await client.requestRaw({ path: "/recordings/abc/stream", method: "GET" });
    assert.equal(res.status, 200);
    assert.equal(res.headers.get("content-type"), "audio/wav");
    const buf = new Uint8Array(await res.arrayBuffer());
    assert.deepEqual([...buf], [0x52, 0x49, 0x46, 0x46]);
    // data(401) → refresh(204) → data(200)
    assert.equal(calls.length, 3);
    assert.ok(calls[2]!.url.endsWith("/recordings/abc/stream"));

    (globalThis as unknown as { fetch?: unknown }).fetch = prevFetch;
});

test("requestRaw surfaces the backend error detail on a non-OK response", async () => {
    __resetRefreshStateForTests();
    const prevFetch = (globalThis as unknown as { fetch?: unknown }).fetch;
    (globalThis as unknown as { fetch: unknown }).fetch = (async (url: string) => {
        if (url.endsWith("/auth/refresh")) return new Response(null, { status: 401 });
        return new Response(JSON.stringify({ detail: "Invalid CSV format" }), {
            status: 400,
            headers: { "content-type": "application/json" },
        });
    }) as unknown as typeof fetch;

    const client = createHttpClient({ baseUrl: "http://example.test" });
    let caught: unknown;
    try {
        await client.requestRaw({ path: "/contacts/campaigns/c1/upload", method: "POST" });
    } catch (e) {
        caught = e;
    }
    assert.ok(caught instanceof ApiClientError);
    assert.equal((caught as ApiClientError).status, 400);
    assert.equal((caught as ApiClientError).message, "Invalid CSV format");

    (globalThis as unknown as { fetch?: unknown }).fetch = prevFetch;
});

