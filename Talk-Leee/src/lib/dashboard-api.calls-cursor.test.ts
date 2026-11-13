import assert from "node:assert/strict";
import { test } from "node:test";

// QA F6-03. Wire contract: backend/app/api/v1/endpoints/calls.py list_calls and
// backend/docs/call_history_pagination.md — `pagination=cursor` skips the
// total count and the OFFSET scan; the response carries `next_cursor`, null on
// the last page. A cursor is bound to the tenant and the exact filter set.
function row(id: string, direction: "inbound" | "outbound" = "outbound") {
    return {
        id,
        timestamp: "2026-11-06T10:00:00Z",
        from_number: "+14155550111",
        to_number: "+14155550222",
        status: "completed",
        direction,
        campaign_id: "agent-1",
    };
}

async function withFetch(handler: (url: URL) => unknown, run: (urls: URL[]) => Promise<void>) {
    const previousFetch = globalThis.fetch;
    const urls: URL[] = [];
    process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1";
    globalThis.fetch = (async (input: RequestInfo | URL) => {
        const url = new URL(String(input));
        urls.push(url);
        return new Response(JSON.stringify(handler(url)), { status: 200, headers: { "content-type": "application/json" } });
    }) as typeof fetch;
    try {
        await run(urls);
    } finally {
        globalThis.fetch = previousFetch;
    }
}

test("the first call-history page is requested in cursor mode with the filters and no page number", async () => {
    await withFetch(() => ({ items: [row("call-1")], total: null, next_cursor: "cursor-2" }), async (urls) => {
        const { dashboardApi } = await import("@/lib/dashboard-api");
        const first = await dashboardApi.listCallsCursor(null, 50, { direction: "inbound", inboundCampaignId: "inbound-1" });

        const query = urls[0]!.searchParams;
        assert.equal(urls[0]!.pathname, "/api/v1/calls");
        assert.equal(query.get("pagination"), "cursor");
        assert.equal(query.get("page_size"), "50");
        assert.equal(query.get("direction"), "inbound");
        assert.equal(query.get("inbound_campaign_id"), "inbound-1");
        assert.equal(query.has("cursor"), false, "the first page has no cursor");
        assert.equal(query.has("page"), false, "cursor mode must not carry a page number");
        assert.equal(first.calls[0]?.id, "call-1");
        assert.equal(first.nextCursor, "cursor-2");
    });
});

test("the next page passes the returned cursor and replays the same filters", async () => {
    const chain: Record<string, unknown> = {
        "": { items: [row("call-1")], total: null, next_cursor: "cursor-2" },
        "cursor-2": { items: [row("call-2")], total: null, next_cursor: "cursor-3" },
        "cursor-3": { items: [row("call-3")], total: null, next_cursor: null },
    };
    await withFetch((url) => chain[url.searchParams.get("cursor") ?? ""], async (urls) => {
        const { dashboardApi } = await import("@/lib/dashboard-api");
        const filters = { direction: "outbound" as const };
        let cursor: string | null = null;
        const ids: string[] = [];
        do {
            const page: Awaited<ReturnType<typeof dashboardApi.listCallsCursor>> = await dashboardApi.listCallsCursor(cursor, 100, filters);
            ids.push(...page.calls.map((call) => call.id));
            cursor = page.nextCursor;
        } while (cursor);

        assert.deepEqual(ids, ["call-1", "call-2", "call-3"]);
        assert.deepEqual(urls.map((url) => url.searchParams.get("cursor")), [null, "cursor-2", "cursor-3"]);
        for (const url of urls) {
            assert.equal(url.searchParams.get("pagination"), "cursor");
            assert.equal(url.searchParams.get("page_size"), "100");
            assert.equal(url.searchParams.get("direction"), "outbound", "every continuation replays the filter");
            assert.equal(url.searchParams.has("page"), false);
        }
    });
});

test("a response without a continuation cursor ends the list and carries no invented total", async () => {
    await withFetch(() => ({ items: [row("call-1")], total: null }), async () => {
        const { dashboardApi } = await import("@/lib/dashboard-api");
        const last = await dashboardApi.listCallsCursor(null, 50);
        assert.equal(last.nextCursor, null);
        assert.equal("total" in last, false);
    });
});

test("the cursor reaches the server exactly as issued", async () => {
    // The server issues unpadded URL-safe base64 (call_history_cursor.py).
    const issued = "eyJ2IjoxLCJpZCI6IjAwMDAwMDAwLTAwMDAtNDAwMC04MDAwLTAwMDAwMDAwMDAwMSJ9";
    await withFetch(() => ({ items: [], total: null, next_cursor: null }), async (urls) => {
        const { dashboardApi } = await import("@/lib/dashboard-api");
        await dashboardApi.listCallsCursor(issued, 50);
        assert.equal(urls[0]!.searchParams.get("cursor"), issued);
    });
});
