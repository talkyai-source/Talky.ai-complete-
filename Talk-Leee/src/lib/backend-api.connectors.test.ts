import { test } from "node:test";
import assert from "node:assert/strict";

test("backendApi.connectors.authorize calls authorize endpoint with redirect_uri", async () => {
    const prevFetch = globalThis.fetch;
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(url), init });
        return new Response(JSON.stringify({ authorization_url: "https://provider.example/authorize" }), {
            status: 200,
            headers: { "content-type": "application/json" },
        });
    }) as typeof fetch;

    try {
        process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1";
        const { backendApi } = await import("@/lib/backend-api");
        const res = await backendApi.connectors.authorize({
            type: "calendar",
            redirect_uri: "http://localhost:3000/connectors/callback?type=calendar",
        });
        assert.equal(res.authorization_url, "https://provider.example/authorize");
        assert.equal(calls.length, 1);
        assert.match(calls[0]!.url, /\/connectors\/calendar\/authorize\?/);
        assert.match(calls[0]!.url, /redirect_uri=/);
    } finally {
        globalThis.fetch = prevFetch;
    }
});

test("backendApi.connectors.disconnect calls disconnect endpoint via POST", async () => {
    const prevFetch = globalThis.fetch;
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(url), init });
        return new Response("", { status: 200, headers: { "content-type": "text/plain" } });
    }) as typeof fetch;

    try {
        process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1";
        const { backendApi } = await import("@/lib/backend-api");
        await backendApi.connectors.disconnect({ type: "drive" });
        assert.equal(calls.length, 1);
        assert.match(calls[0]!.url, /\/connectors\/drive\/disconnect$/);
        assert.equal(calls[0]!.init?.method, "POST");
    } finally {
        globalThis.fetch = prevFetch;
    }
});

test("backendApi.connectors.status parses connector statuses", async () => {
    const prevFetch = globalThis.fetch;
    globalThis.fetch = (async () => {
        return new Response(
            JSON.stringify({
                items: [
                    { type: "calendar", status: "connected", last_sync: "2025-01-01T00:00:00Z" },
                    { type: "email", status: "error", error_message: "Invalid grant" },
                ],
            }),
            {
                status: 200,
                headers: { "content-type": "application/json" },
            }
        );
    }) as typeof fetch;

    try {
        process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1";
        const { backendApi } = await import("@/lib/backend-api");
        const res = await backendApi.connectors.status();
        assert.equal(res.items.length, 2);
        assert.equal(res.items[0]!.type, "calendar");
        assert.equal(res.items[1]!.status, "error");
    } finally {
        globalThis.fetch = prevFetch;
    }
});
