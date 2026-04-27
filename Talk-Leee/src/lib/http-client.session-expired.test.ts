import { test } from "node:test";
import assert from "node:assert/strict";
import {
    createHttpClient,
    resetSessionExpiredLatch,
    setSessionExpiredHandler,
} from "@/lib/http-client";

/**
 * The single-source-of-truth 401 redirect.
 *
 * Before the unification, every page that called the API directly with
 * try/catch had its own ad-hoc 401 check.  Some redirected, others showed
 * a red message and stayed on the screen — that's the bug these tests
 * are guarding against.
 */

function withFakeFetch(status: number, run: () => Promise<void>) {
    return async () => {
        const prev = (globalThis as unknown as { fetch?: unknown }).fetch;
        (globalThis as unknown as { fetch: unknown }) = {
            fetch: (async () =>
                new Response(JSON.stringify({ detail: "expired" }), {
                    status,
                    headers: { "content-type": "application/json" },
                })) as unknown as typeof fetch,
        };
        try {
            await run();
        } finally {
            (globalThis as unknown as { fetch?: unknown }).fetch = prev;
        }
    };
}

test("401 fires the registered session-expired handler and clears the token", async () => {
    let stored: string | null = "valid-token";
    const setToken = (t: string | null) => {
        stored = t;
    };

    let handlerCalls = 0;
    setSessionExpiredHandler(() => {
        handlerCalls++;
    });
    resetSessionExpiredLatch();

    const prev = (globalThis as unknown as { fetch?: unknown }).fetch;
    (globalThis as unknown as { fetch: unknown }).fetch = (async () =>
        new Response(JSON.stringify({ detail: "expired" }), {
            status: 401,
            headers: { "content-type": "application/json" },
        })) as unknown as typeof fetch;

    try {
        const client = createHttpClient({
            baseUrl: "http://example.test",
            getToken: () => stored,
            setToken,
        });
        await assert.rejects(() => client.request({ path: "/x" }));
    } finally {
        (globalThis as unknown as { fetch?: unknown }).fetch = prev;
        setSessionExpiredHandler(null);
    }

    assert.equal(handlerCalls, 1, "handler should have fired exactly once");
    assert.equal(stored, null, "token should have been cleared");
});

test("multiple parallel 401s fire the handler only ONCE (idempotent)", async () => {
    let handlerCalls = 0;
    setSessionExpiredHandler(() => {
        handlerCalls++;
    });
    resetSessionExpiredLatch();

    const prev = (globalThis as unknown as { fetch?: unknown }).fetch;
    (globalThis as unknown as { fetch: unknown }).fetch = (async () =>
        new Response(JSON.stringify({ detail: "expired" }), {
            status: 401,
            headers: { "content-type": "application/json" },
        })) as unknown as typeof fetch;

    try {
        const client = createHttpClient({
            baseUrl: "http://example.test",
            getToken: () => "x",
            setToken: () => {},
        });
        // 5 in flight at the same moment — simulating what happens
        // when a logged-out user opens a page that fan-outs several
        // queries in parallel.
        await Promise.all([
            client.request({ path: "/a" }).catch(() => null),
            client.request({ path: "/b" }).catch(() => null),
            client.request({ path: "/c" }).catch(() => null),
            client.request({ path: "/d" }).catch(() => null),
            client.request({ path: "/e" }).catch(() => null),
        ]);
    } finally {
        (globalThis as unknown as { fetch?: unknown }).fetch = prev;
        setSessionExpiredHandler(null);
    }

    assert.equal(
        handlerCalls,
        1,
        "the latch must debounce parallel 401s into a single redirect",
    );
});

test("resetSessionExpiredLatch re-arms the handler after a successful login", async () => {
    let handlerCalls = 0;
    setSessionExpiredHandler(() => {
        handlerCalls++;
    });
    resetSessionExpiredLatch();

    const prev = (globalThis as unknown as { fetch?: unknown }).fetch;
    (globalThis as unknown as { fetch: unknown }).fetch = (async () =>
        new Response(JSON.stringify({ detail: "expired" }), {
            status: 401,
            headers: { "content-type": "application/json" },
        })) as unknown as typeof fetch;

    try {
        const client = createHttpClient({
            baseUrl: "http://example.test",
            getToken: () => "x",
            setToken: () => {},
        });

        // First 401 → fires.
        await client.request({ path: "/a" }).catch(() => null);
        // Second 401 BEFORE re-arm → debounced.
        await client.request({ path: "/b" }).catch(() => null);
        assert.equal(handlerCalls, 1);

        // Simulate a successful login — token re-set, latch re-armed.
        resetSessionExpiredLatch();

        // Next 401 → fires again.
        await client.request({ path: "/c" }).catch(() => null);
        assert.equal(handlerCalls, 2);
    } finally {
        (globalThis as unknown as { fetch?: unknown }).fetch = prev;
        setSessionExpiredHandler(null);
    }
});

test("non-401 errors do NOT fire the session-expired handler", async () => {
    let handlerCalls = 0;
    setSessionExpiredHandler(() => {
        handlerCalls++;
    });
    resetSessionExpiredLatch();

    const prev = (globalThis as unknown as { fetch?: unknown }).fetch;
    (globalThis as unknown as { fetch: unknown }).fetch = (async () =>
        new Response(JSON.stringify({ detail: "boom" }), {
            status: 500,
            headers: { "content-type": "application/json" },
        })) as unknown as typeof fetch;

    try {
        const client = createHttpClient({
            baseUrl: "http://example.test",
            getToken: () => "x",
            setToken: () => {},
        });
        await client.request({ path: "/a" }).catch(() => null);
        await client.request({ path: "/b" }).catch(() => null);
    } finally {
        (globalThis as unknown as { fetch?: unknown }).fetch = prev;
        setSessionExpiredHandler(null);
    }

    assert.equal(handlerCalls, 0, "500s and other errors must NOT redirect");
});

test("a thrown handler does not derail the request rejection", async () => {
    setSessionExpiredHandler(() => {
        throw new Error("simulated handler crash");
    });
    resetSessionExpiredLatch();

    const prev = (globalThis as unknown as { fetch?: unknown }).fetch;
    (globalThis as unknown as { fetch: unknown }).fetch = (async () =>
        new Response("nope", { status: 401 })) as unknown as typeof fetch;

    try {
        const client = createHttpClient({
            baseUrl: "http://example.test",
            getToken: () => "x",
            setToken: () => {},
        });
        // The request must still reject as ApiClientError("unauthorized")
        // even though the handler threw.
        let caught: unknown;
        try {
            await client.request({ path: "/x" });
        } catch (e) {
            caught = e;
        }
        assert.ok(caught instanceof Error);
        assert.equal((caught as { code?: string }).code, "unauthorized");
    } finally {
        (globalThis as unknown as { fetch?: unknown }).fetch = prev;
        setSessionExpiredHandler(null);
    }
});
