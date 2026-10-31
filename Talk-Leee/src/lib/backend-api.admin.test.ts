import assert from "node:assert/strict";
import { test } from "node:test";
import { auditRow, EVENT_TIME, ACTOR_ID, TENANT_ID, PARTNER_ID } from "@/test-utils/admin-events-fixtures";

test("backendApi.admin.auditLogs.list uses the canonical raw route and supported query", async () => {
    const prevFetch = globalThis.fetch;
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(url), init });
        return new Response(JSON.stringify([auditRow()]), { status: 200, headers: { "content-type": "application/json" } });
    }) as typeof fetch;
    try {
        const { backendApi } = await import("@/lib/backend-api");
        const result = await backendApi.admin.auditLogs.list({ pageSize: 20, eventGroup: "role_change",
            before: { time: EVENT_TIME, id: auditRow(20).event_id }, userQuery: "alice@example.com", tenantId: TENANT_ID, partnerId: PARTNER_ID });
        assert.equal(result.items.length, 1); assert.equal(result.items[0]!.actor.id, ACTOR_ID);
        const url = new URL(calls[0]!.url);
        assert.equal(url.pathname, "/api/v1/admin/audit/logs");
        assert.equal(url.searchParams.get("limit"), "21"); assert.equal(url.searchParams.has("offset"), false);
        assert.equal(url.searchParams.get("before_time"), EVENT_TIME); assert.equal(url.searchParams.get("before_id"), auditRow(20).event_id);
        assert.equal(url.searchParams.get("event_group"), "role_change"); assert.equal(url.searchParams.get("user_query"), "alice@example.com");
        assert.equal(url.searchParams.get("tenant_id"), TENANT_ID); assert.equal(url.searchParams.get("partner_id"), PARTNER_ID);
        assert.equal(calls[0]!.init?.method, "GET");
    } finally { globalThis.fetch = prevFetch; }
});

test("backendApi.admin.tenants.suspend posts to suspension endpoint", async () => {
    const prevFetch = globalThis.fetch;
    const calls: Array<{ url: string; init?: RequestInit }> = [];

    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(url), init });
        return new Response(
            JSON.stringify({
                id: "tenant-7",
                status: "suspended",
                updated_at: "2026-03-02T15:00:00.000Z",
                changed: true,
                audit_event_id: "00000000-0000-4000-8000-000000000007",
            }),
            { status: 200, headers: { "content-type": "application/json" } }
        );
    }) as typeof fetch;

    try {
        process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1";
        const { backendApi } = await import("@/lib/backend-api");

        const result = await backendApi.admin.tenants.suspend({
            tenantId: "tenant-7",
            expectedUpdatedAt: "2026-03-01T15:00:00.000Z",
            reason: "critical_abuse",
        });

        assert.equal(result.status, "suspended");
        assert.equal(calls.length, 1);
        assert.match(calls[0]!.url, /\/admin\/tenants\/tenant-7\/suspend$/);
        assert.equal(calls[0]!.init?.method, "POST");
        assert.equal(calls[0]!.init?.body, JSON.stringify({ expected_updated_at: "2026-03-01T15:00:00.000Z", reason: "critical_abuse" }));
    } finally {
        globalThis.fetch = prevFetch;
    }
});

test("tenant list consumes the actual array, business names, statuses and reviewed versions", async () => {
    const previous = globalThis.fetch;
    const calls: string[] = [];
    globalThis.fetch = (async (url) => {
        calls.push(String(url));
        return new Response(JSON.stringify([
            { id: "tenant-1", business_name: "Northwind", status: "trialing", updated_at: null },
            { id: "tenant-2", business_name: "Closed", status: "cancelled", updated_at: "2026-03-02T15:00:00.000Z" },
        ]), { status: 200, headers: { "content-type": "application/json" } });
    }) as typeof fetch;
    try {
        const { backendApi } = await import("@/lib/backend-api");
        const result = await backendApi.admin.tenants.list({ page: 3, pageSize: 8, query: "North" });
        const url = new URL(calls[0]!);
        assert.equal(url.searchParams.get("offset"), "16");
        assert.equal(url.searchParams.get("limit"), "9");
        assert.equal(url.searchParams.get("search"), "North");
        assert.equal(url.searchParams.get("include_archived"), "true");
        assert.equal(url.searchParams.has("page"), false);
        assert.equal(result.items[0]!.name, "Northwind");
        assert.equal(result.items[0]!.status, "trialing");
        assert.equal(result.items[0]!.updatedAt, null);
        assert.equal(result.items[1]!.status, "cancelled");
    } finally { globalThis.fetch = previous; }
});

test("resume uses the mounted route and validates committed receipt identity", async () => {
    const previous = globalThis.fetch;
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    let identity = "tenant-7";
    globalThis.fetch = (async (url, init) => {
        calls.push({ url: String(url), init });
        return new Response(JSON.stringify({ id: identity, status: "active", changed: false,
            updated_at: null, audit_event_id: null }), { status: 200, headers: { "content-type": "application/json" } });
    }) as typeof fetch;
    try {
        const { backendApi } = await import("@/lib/backend-api");
        const result = await backendApi.admin.tenants.reactivate({ tenantId: "tenant-7", expectedUpdatedAt: null });
        assert.equal(result.status, "active");
        assert.match(calls[0]!.url, /\/admin\/tenants\/tenant-7\/resume$/);
        assert.deepEqual(JSON.parse(String(calls[0]!.init?.body)), { expected_updated_at: null });
        identity = "different-tenant";
        await assert.rejects(backendApi.admin.tenants.reactivate({ tenantId: "tenant-7", expectedUpdatedAt: null }), /could not be confirmed/);
    } finally { globalThis.fetch = previous; }
});

test("missing versions and unsupported partner filters fail before network writes or unscoped reads", async () => {
    const previous = globalThis.fetch;
    let calls = 0;
    globalThis.fetch = (async () => { calls++; throw new Error("Unexpected network"); }) as typeof fetch;
    try {
        const { backendApi } = await import("@/lib/backend-api");
        // Explicitly exercise an untyped/older consumer at the runtime boundary.
        await assert.rejects(backendApi.admin.tenants.suspend({ tenantId: "tenant-7", expectedUpdatedAt: undefined as unknown as null }), /Refresh and review/);
        await assert.rejects(backendApi.admin.tenants.list({ partnerId: "partner-1" }), /Partner filtering/);
        assert.equal(calls, 0);
    } finally { globalThis.fetch = previous; }
});

test("changed tenant response without durable audit ID is unconfirmed", async () => {
    const previous = globalThis.fetch;
    globalThis.fetch = (async () => new Response(JSON.stringify({ id: "tenant-7", status: "suspended",
        changed: true, audit_event_id: null, updated_at: null }), { status: 200,
        headers: { "content-type": "application/json" } })) as typeof fetch;
    try {
        const { backendApi } = await import("@/lib/backend-api");
        await assert.rejects(backendApi.admin.tenants.suspend({ tenantId: "tenant-7", expectedUpdatedAt: null }), /durable audit receipt/);
    } finally { globalThis.fetch = previous; }
});
