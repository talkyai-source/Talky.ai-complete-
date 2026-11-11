import { mock, test } from "node:test";
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

test("backendApi.connectors.status is ended by the client timeout when the server never answers", async () => {
    const prevFetch = globalThis.fetch;
    // A server that never answers: the request only ends if the client aborts it.
    globalThis.fetch = ((_url: RequestInfo | URL, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () => reject(init.signal!.reason));
        })) as typeof fetch;
    const settle = async () => {
        for (let i = 0; i < 5; i += 1) await new Promise((r) => setImmediate(r));
    };

    mock.timers.enable({ apis: ["setTimeout"] });
    try {
        process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1";
        const { backendApi } = await import("@/lib/backend-api");
        let outcome: unknown = "waiting";
        const done = backendApi.connectors.status().then(
            () => { outcome = "answered"; },
            (err: unknown) => { outcome = err; }
        );
        await settle();
        mock.timers.tick(11_999);
        await settle();
        assert.equal(outcome, "waiting", "still inside the 12 s budget");

        mock.timers.tick(1);
        await settle();
        assert.equal((outcome as { code?: string }).code, "timeout");
        assert.equal((outcome as { message?: string }).message, "Request timed out");
        void done;
    } finally {
        mock.timers.reset();
        globalThis.fetch = prevFetch;
    }
});

// Headers arrive, then the body never finishes: the client's own timeout has
// already been cleared at that point, so only the 20 s whole-request deadline
// can end these two requests.
function stalledBodyFetch() {
    return (async () =>
        new Response(new ReadableStream({ start() {} }), { status: 200, headers: { "content-type": "application/json" } })) as typeof fetch;
}

const STALLED: Array<[string, (api: typeof import("@/lib/backend-api").backendApi) => Promise<unknown>]> = [
    ["GET /connectors/status", (api) => api.connectors.status()],
    ["the Connect/Reconnect authorize request", (api) => api.connectors.authorize({ type: "calendar", redirect_uri: "http://localhost:3000/connectors/calendar/callback" })],
];

for (const [label, call] of STALLED) {
    test(`${label} is ended as a failure 20 seconds after it started when the response body stalls`, async () => {
        const prevFetch = globalThis.fetch;
        globalThis.fetch = stalledBodyFetch();
        const settle = async () => {
            for (let i = 0; i < 5; i += 1) await new Promise((r) => setImmediate(r));
        };

        mock.timers.enable({ apis: ["setTimeout"] });
        try {
            process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1";
            const { backendApi } = await import("@/lib/backend-api");
            let outcome: unknown = "waiting";
            void call(backendApi).then(
                () => { outcome = "answered"; },
                (err: unknown) => { outcome = err; }
            );
            await settle();
            mock.timers.tick(19_999);
            await settle();
            assert.equal(outcome, "waiting", "not ended before 20 s (and not by the 12 s header timeout)");

            mock.timers.tick(1);
            await settle();
            assert.equal((outcome as { name?: string }).name, "ApiClientError");
            assert.equal((outcome as { code?: string }).code, "timeout");
            assert.equal((outcome as { message?: string }).message, "Request timed out");
        } finally {
            mock.timers.reset();
            globalThis.fetch = prevFetch;
        }
    });
}

test("a connector request that answers in time is not touched by the 20 s deadline", async () => {
    const prevFetch = globalThis.fetch;
    globalThis.fetch = (async () =>
        new Response(JSON.stringify({ items: [{ type: "drive", status: "disconnected" }] }), { status: 200, headers: { "content-type": "application/json" } })) as typeof fetch;
    mock.timers.enable({ apis: ["setTimeout"] });
    try {
        process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1";
        const { backendApi } = await import("@/lib/backend-api");
        const res = await backendApi.connectors.status();
        assert.equal(res.items[0]!.status, "disconnected");
        // The deadline timer was cleared: advancing past it rejects nothing.
        mock.timers.tick(60_000);
        await new Promise((r) => setImmediate(r));
    } finally {
        mock.timers.reset();
        globalThis.fetch = prevFetch;
    }
});
