import { test } from "node:test";
import assert from "node:assert/strict";

const receipt = { id: "action-owned-1", type: "send_email", status: "unknown", triggered_by: "assistant_tool",
    lead_id: "lead-1", created_at: "2026-10-05T00:00:00Z", completed_at: null,
    confirmation_allowed: false, receipt: { provider: "gmail", external_account_id: "reviewed-account", message_id: "original-message", provider_status: "accepted", child_action_id: "inner-receipt" },
    input_data: { body: "must not be returned by the normalized client" } };

test("action history reads the actual actions envelope and retains an unknown result", async () => {
    const previous = globalThis.fetch;
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = (async (url, init) => {
        calls.push({ url: String(url), init });
        return Response.json({ actions: [receipt], total: 1, page: 1, page_size: 50 });
    }) as typeof fetch;
    try {
        process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1";
        const { backendApi } = await import("@/lib/backend-api");
        const result = await backendApi.assistantRuns.list({ page: 1, pageSize: 50, statuses: ["unknown"],
            actionType: "send_email", leadId: "lead-1", from: "2026-10-01T00:00:00Z", to: "2026-10-05T23:59:59Z",
            sortKey: "createdAt", sortDir: "desc" });
        assert.equal(result.items[0]?.status, "unknown");
        assert.equal(result.items[0]?.receipt?.message_id, "original-message");
        assert.equal(result.items[0]?.receipt?.provider_status, "accepted");
        assert.equal(result.items[0]?.receipt?.child_action_id, "inner-receipt");
        assert.equal(result.items[0]?.confirmationAllowed, false);
        assert.equal("requestPayload" in result.items[0]!, false);
        assert.equal(result.total, 1);
        assert.equal(calls.length, 1);
        const url = new URL(calls[0]!.url);
        assert.equal(url.pathname, "/api/v1/assistant/actions");
        assert.equal(url.searchParams.get("type"), "send_email");
        assert.equal(url.searchParams.get("status"), "unknown");
        assert.equal(url.searchParams.get("sort_by"), "created_at");
        assert.equal(url.searchParams.get("from_date"), "2026-10-01T00:00:00Z");
        assert.equal(url.searchParams.get("lead_id"), "lead-1");
        assert.equal(calls[0]!.init?.method, "GET");
    } finally { globalThis.fetch = previous; }
});

test("an invalid history envelope fails visibly instead of appearing as an empty success", async () => {
    const previous = globalThis.fetch;
    globalThis.fetch = (async () => Response.json({ items: [], total: 0 })) as typeof fetch;
    try {
        const { backendApi } = await import("@/lib/backend-api");
        await assert.rejects(backendApi.assistantRuns.list({ page: 2 }));
    } finally { globalThis.fetch = previous; }
});

test("unsupported generic plan, execution and blind retry clients are not exposed", async () => {
    const { backendApi } = await import("@/lib/backend-api");
    const { backendEndpoints } = await import("@/lib/backend-endpoints");
    assert.equal("assistant" in backendApi, false);
    assert.equal("retry" in backendApi.assistantRuns, false);
    assert.equal("assistantActions" in backendApi, false);
    for (const endpoint of Object.values(backendEndpoints)) {
        assert.doesNotMatch(endpoint.path, /^\/assistant\/(runs|plan|execute)(?:\/|$)/);
    }
});
