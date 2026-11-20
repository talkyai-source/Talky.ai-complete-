import assert from "node:assert/strict";
import { test } from "node:test";

async function withServer<T>(body: (url: string) => unknown, run: (api: typeof import("@/lib/dashboard-api").dashboardApi) => Promise<T>): Promise<T> {
    const previousFetch = globalThis.fetch;
    process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1";
    globalThis.fetch = (async (url: RequestInfo | URL) =>
        new Response(JSON.stringify(body(String(url))), { status: 200, headers: { "content-type": "application/json" } })) as typeof fetch;
    try {
        const { dashboardApi } = await import("@/lib/dashboard-api");
        return await run(dashboardApi);
    } finally {
        globalThis.fetch = previousFetch;
    }
}
const item = { id: "call-1", timestamp: "2026-08-26T10:00:00Z", to_number: "+14155550222", status: "completed", direction: "outbound", campaign_id: "camp-1" };

test("getCall carries the summary verdict as lead_outcome", async () => {
    const call = await withServer(() => ({ ...item, summary_json: { outcome: "qualified | wants a demo" } }), (api) => api.getCall("call-1"));
    assert.equal(call.lead_outcome, "qualified | wants a demo");
});

test("getCall without a summary verdict has no lead_outcome", async () => {
    const call = await withServer(() => item, (api) => api.getCall("call-1"));
    assert.equal(call.lead_outcome ?? null, null);
});

test("the call list carries best_time_to_call", async () => {
    const list = await withServer(() => ({ items: [{ ...item, best_time_to_call: "weekday mornings" }, { ...item, id: "call-2" }], total: 2 }), (api) => api.listCalls(1, 20));
    assert.equal(list.calls[0]?.best_time_to_call, "weekday mornings");
    assert.equal(list.calls[1]?.best_time_to_call, null);
});
