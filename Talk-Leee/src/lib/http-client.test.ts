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

test("http client reads an application/problem+json refusal and shows its detail", async () => {
    // 2026-10-14: the PBX endpoints answer RFC 9457 problem+json, and every
    // refusal (line in use, tested too often, duplicate name) read "Request failed".
    const prevFetch = (globalThis as unknown as { fetch?: unknown }).fetch;
    (globalThis as unknown as { fetch: unknown }).fetch = (async () => {
        return new Response(JSON.stringify({
            type: "about:blank", title: "Trunk In Use", status: 409,
            detail: "This shared line is still assigned to another account.", instance: "/x",
        }), { status: 409, headers: { "content-type": "application/problem+json" } });
    }) as unknown as typeof fetch;

    const client = createHttpClient({ baseUrl: "http://example.test" });
    let caught: unknown;
    try {
        await client.request({ path: "/x", method: "DELETE" });
    } catch (e) {
        caught = e;
    } finally {
        (globalThis as unknown as { fetch?: unknown }).fetch = prevFetch;
    }

    assert.ok(caught instanceof ApiClientError);
    const err = caught as ApiClientError;
    assert.equal(err.status, 409);
    assert.equal(err.message, "This shared line is still assigned to another account.");
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

test("http client preserves structured FastAPI detail codes", async () => {
    const prevFetch = (globalThis as unknown as { fetch?: unknown }).fetch;
    (globalThis as unknown as { fetch: unknown }).fetch = (async () => new Response(
        JSON.stringify({
            detail: {
                code: "recording_delete_storage_failed",
                message: "Recording bytes could not be removed",
                details: { retryable: true },
            },
        }),
        { status: 503, headers: { "content-type": "application/json" } },
    )) as unknown as typeof fetch;

    try {
        const client = createHttpClient({ baseUrl: "http://example.test" });
        await assert.rejects(
            client.request({ path: "/recordings/abc", method: "DELETE" }),
            (error: unknown) => {
                assert.ok(error instanceof ApiClientError);
                assert.equal(error.code, "recording_delete_storage_failed");
                assert.equal(error.message, "Recording bytes could not be removed");
                assert.deepEqual(error.details, { retryable: true });
                return true;
            },
        );
    } finally {
        (globalThis as unknown as { fetch?: unknown }).fetch = prevFetch;
    }
});

test("requestRaw preserves canonical error codes for binary endpoints", async () => {
    const prevFetch = (globalThis as unknown as { fetch?: unknown }).fetch;
    (globalThis as unknown as { fetch: unknown }).fetch = (async () => new Response(
        JSON.stringify({
            error: {
                code: "permission_denied",
                message: "Recording download is not allowed",
                details: { required: "recordings:download" },
            },
        }),
        { status: 403, headers: { "content-type": "application/json" } },
    )) as unknown as typeof fetch;

    try {
        const client = createHttpClient({ baseUrl: "http://example.test" });
        await assert.rejects(
            client.requestRaw({ path: "/recordings/abc/download" }),
            (error: unknown) => {
                assert.ok(error instanceof ApiClientError);
                assert.equal(error.code, "permission_denied");
                assert.equal(error.message, "Recording download is not allowed");
                assert.deepEqual(error.details, { required: "recordings:download" });
                return true;
            },
        );
    } finally {
        (globalThis as unknown as { fetch?: unknown }).fetch = prevFetch;
    }
});


async function refusal(body: unknown, status = 422, raw = false): Promise<ApiClientError> {
    __resetRefreshStateForTests();
    const prevFetch = (globalThis as unknown as { fetch?: unknown }).fetch;
    (globalThis as unknown as { fetch: unknown }).fetch = (async () =>
        new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } })) as unknown as typeof fetch;
    try {
        const client = createHttpClient({ baseUrl: "http://example.test" });
        await (raw ? client.requestRaw({ path: "/x", method: "POST" }) : client.request({ path: "/x", method: "POST" }));
    } catch (e) {
        assert.ok(e instanceof ApiClientError);
        return e;
    } finally {
        (globalThis as unknown as { fetch?: unknown }).fetch = prevFetch;
    }
    throw new Error("expected a refusal");
}

const GENERIC = { code: "validation_error", message: "Request payload failed validation" };

test("a 422 envelope names the refused field and the server's reason", async () => {
    const err = await refusal({
        error: {
            ...GENERIC,
            details: [{ type: "string_too_long", loc: ["body", "business_name"], msg: "String should have at most 100 characters" }],
        },
    });
    assert.equal(err.message, "Business name: String should have at most 100 characters");
    assert.equal(err.code, "validation_error");
});

test("a 422 shows at most three fields and strips the 'Value error,' prefix", async () => {
    const err = await refusal({
        error: {
            ...GENERIC,
            details: [
                { type: "value_error", loc: ["body", "name"], msg: "Value error, Name must not be blank." },
                { type: "x", loc: ["body", "email"], msg: "Bad email" },
                { type: "x", loc: ["body", "business_name"], msg: "Too long" },
                { type: "x", loc: ["body", "phone"], msg: "Fourth" },
            ],
        },
    });
    assert.equal(err.message, "Name: Name must not be blank.; Email: Bad email; Business name: Too long");
});

test("a raw FastAPI 422 list and requestRaw read the same way", async () => {
    const list = { detail: [{ type: "x", loc: ["body", "email"], msg: "value is not a valid email address" }] };
    assert.equal((await refusal(list)).message, "Email: value is not a valid email address");
    assert.equal((await refusal(list, 422, true)).message, "Email: value is not a valid email address");
});

test("a 422 without field errors, or a non-422, keeps its own message", async () => {
    assert.equal((await refusal({ error: { ...GENERIC, details: [] } })).message, "Request payload failed validation");
    assert.equal((await refusal({ error: { code: "legal_acceptance_required", message: "Accept the terms", details: [{ loc: ["body", "x"], msg: "no" }] } })).message, "Accept the terms");
    assert.equal((await refusal({ error: { ...GENERIC, details: [{ loc: ["body", "x"], msg: "no" }] } }, 400)).message, "Request payload failed validation");
});
