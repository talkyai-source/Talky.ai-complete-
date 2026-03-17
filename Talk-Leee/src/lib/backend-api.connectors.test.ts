import { test } from "node:test";
import assert from "node:assert/strict";

test("backendApi.connectors.providers parses provider metadata arrays", async () => {
    const prevFetch = globalThis.fetch;
    globalThis.fetch = (async () => {
        return new Response(
            JSON.stringify([
                { provider: "google_calendar", type: "calendar", name: "Google Calendar", description: "Calendar sync", requires_oauth: true },
                { provider: "outlook_calendar", type: "calendar", name: "Microsoft Outlook", description: "Outlook sync", requires_oauth: true },
            ]),
            {
                status: 200,
                headers: { "content-type": "application/json" },
            }
        );
    }) as typeof fetch;

    try {
        process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1";
        const { backendApi } = await import("@/lib/backend-api");
        const res = await backendApi.connectors.providers();
        assert.equal(res.items.length, 2);
        assert.equal(res.items[0]!.provider, "google_calendar");
        assert.equal(res.items[1]!.name, "Microsoft Outlook");
    } finally {
        globalThis.fetch = prevFetch;
    }
});

test("backendApi.connectors.authorizeProvider posts provider selection to authorize endpoint", async () => {
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
        const res = await backendApi.connectors.authorizeProvider({
            type: "calendar",
            provider: "google_calendar",
            name: "Google Calendar",
        });
        assert.equal(res.authorization_url, "https://provider.example/authorize");
        assert.equal(calls.length, 1);
        assert.match(calls[0]!.url, /\/connectors\/authorize$/);
        assert.equal(calls[0]!.init?.method, "POST");
        assert.match(String(calls[0]!.init?.body), /"provider":"google_calendar"/);
    } finally {
        globalThis.fetch = prevFetch;
    }
});

test("backendApi.connectors.disconnectById deletes the selected connector", async () => {
    const prevFetch = globalThis.fetch;
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(url), init });
        return new Response("", { status: 200, headers: { "content-type": "text/plain" } });
    }) as typeof fetch;

    try {
        process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1";
        const { backendApi } = await import("@/lib/backend-api");
        await backendApi.connectors.disconnectById({ connectorId: "conn-drive-1" });
        assert.equal(calls.length, 1);
        assert.match(calls[0]!.url, /\/connectors\/conn-drive-1$/);
        assert.equal(calls[0]!.init?.method, "DELETE");
    } finally {
        globalThis.fetch = prevFetch;
    }
});

test("backendApi.connectors.status derives type statuses from connector records", async () => {
    const prevFetch = globalThis.fetch;
    globalThis.fetch = (async () => {
        return new Response(
            JSON.stringify([
                { id: "1", type: "calendar", provider: "google_calendar", status: "active", created_at: "2025-01-01T00:00:00Z" },
                { id: "2", type: "calendar", provider: "outlook_calendar", status: "error", created_at: "2025-01-02T00:00:00Z" },
                { id: "3", type: "email", provider: "gmail", status: "expired", created_at: "2025-01-03T00:00:00Z" },
            ]),
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
        assert.equal(res.items[0]!.status, "connected");
        assert.equal(res.items[1]!.status, "expired");
    } finally {
        globalThis.fetch = prevFetch;
    }
});
