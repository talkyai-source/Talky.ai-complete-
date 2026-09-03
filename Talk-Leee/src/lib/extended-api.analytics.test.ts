import assert from "node:assert/strict";
import test from "node:test";

import { extendedApi } from "@/lib/extended-api";

test("call analytics names the real outbound population by default", async () => {
    const originalFetch = globalThis.fetch;
    const urls: string[] = [];
    globalThis.fetch = (async (url: RequestInfo | URL) => {
        urls.push(String(url));
        return new Response(JSON.stringify({
            series: [],
            direction: "outbound",
            include_tests: false,
        }), { status: 200, headers: { "content-type": "application/json" } });
    }) as typeof fetch;

    try {
        const result = await extendedApi.getCallAnalytics();
        const url = new URL(urls[0]!);
        assert.equal(url.searchParams.get("direction"), "outbound");
        assert.equal(url.searchParams.get("include_tests"), "false");
        assert.equal(result.direction, "outbound");
        assert.equal(result.include_tests, false);
    } finally {
        globalThis.fetch = originalFetch;
    }
});

test("call analytics can request an explicit inbound population", async () => {
    const originalFetch = globalThis.fetch;
    const urls: string[] = [];
    globalThis.fetch = (async (url: RequestInfo | URL) => {
        urls.push(String(url));
        return new Response(JSON.stringify({
            series: [],
            direction: "inbound",
            include_tests: false,
        }), { status: 200, headers: { "content-type": "application/json" } });
    }) as typeof fetch;

    try {
        await extendedApi.getCallAnalytics(undefined, undefined, "day", "inbound", false);
        const url = new URL(urls[0]!);
        assert.equal(url.searchParams.get("direction"), "inbound");
        assert.equal(url.searchParams.get("include_tests"), "false");
    } finally {
        globalThis.fetch = originalFetch;
    }
});
