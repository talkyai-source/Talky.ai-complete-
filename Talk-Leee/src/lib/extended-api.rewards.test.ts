import assert from "node:assert/strict";
import test from "node:test";

import { extendedApi } from "@/lib/extended-api";

test("review reward display is sourced from the verified ledger balance endpoint", async () => {
    const originalFetch = globalThis.fetch;
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(url), init });
        return new Response(JSON.stringify({
            total_points: 20,
            entries: 2,
            awarded_today: 1,
            daily_cap: 5,
            rewards_enabled: true,
        }), { status: 200, headers: { "content-type": "application/json" } });
    }) as typeof fetch;

    try {
        const balance = await extendedApi.getReviewRewardBalance();
        assert.equal(balance.total_points, 20);
        assert.match(calls[0]!.url, /\/calls\/reviews\/rewards\/balance$/);
        assert.equal(calls[0]!.init?.method, "GET");
    } finally {
        globalThis.fetch = originalFetch;
    }
});
