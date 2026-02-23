import { test } from "node:test";
import assert from "node:assert/strict";
import { createHttpClient, ApiClientError } from "@/lib/http-client";

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

