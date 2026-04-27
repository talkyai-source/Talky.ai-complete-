import assert from "node:assert/strict";
import { test } from "node:test";

test("backendApi.admin.auditLogs.list encodes filters and pagination", async () => {
    const prevFetch = globalThis.fetch;
    const calls: Array<{ url: string; init?: RequestInit }> = [];

    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(url), init });
        return new Response(
            JSON.stringify({
                items: [
                    {
                        id: "evt-1",
                        timestamp: "2026-03-01T10:00:00.000Z",
                        actionType: "role_change",
                        actor: { name: "Alice Admin", email: "alice@example.com" },
                        target: { type: "user", id: "user-1", name: "Bob User" },
                        metadata: { old_role: "user", new_role: "tenant_admin" },
                    },
                ],
                total: 1,
                page: 2,
                page_size: 20,
            }),
            { status: 200, headers: { "content-type": "application/json" } }
        );
    }) as typeof fetch;

    try {
        process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1";
        const { backendApi } = await import("@/lib/backend-api");

        const result = await backendApi.admin.auditLogs.list({
            page: 2,
            pageSize: 20,
            eventType: "role_change",
            userQuery: "alice@example.com",
            tenantId: "tenant-1",
            partnerId: "partner-1",
        });

        assert.equal(result.items.length, 1);
        assert.equal(calls.length, 1);
        assert.match(calls[0]!.url, /\/admin\/audit-logs\?/);
        assert.match(calls[0]!.url, /page=2/);
        assert.match(calls[0]!.url, /page_size=20/);
        assert.match(calls[0]!.url, /event_type=role_change/);
        assert.match(calls[0]!.url, /user=alice%40example\.com/);
        assert.match(calls[0]!.url, /tenant_id=tenant-1/);
        assert.match(calls[0]!.url, /partner_id=partner-1/);
        assert.equal(calls[0]!.init?.method, "GET");
    } finally {
        globalThis.fetch = prevFetch;
    }
});

test("backendApi.admin.tenants.suspend posts to suspension endpoint", async () => {
    const prevFetch = globalThis.fetch;
    const calls: Array<{ url: string; init?: RequestInit }> = [];

    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(url), init });
        return new Response(
            JSON.stringify({
                id: "tenant-7",
                tenant_name: "Northwind",
                partner_id: "partner-1",
                status: "suspended",
                updated_at: "2026-03-02T15:00:00.000Z",
            }),
            { status: 200, headers: { "content-type": "application/json" } }
        );
    }) as typeof fetch;

    try {
        process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1";
        const { backendApi } = await import("@/lib/backend-api");

        const result = await backendApi.admin.tenants.suspend({
            tenantId: "tenant-7",
            reason: "critical_abuse",
        });

        assert.equal(result.status, "suspended");
        assert.equal(calls.length, 1);
        assert.match(calls[0]!.url, /\/admin\/tenants\/tenant-7\/suspend$/);
        assert.equal(calls[0]!.init?.method, "POST");
        assert.equal(calls[0]!.init?.body, JSON.stringify({ reason: "critical_abuse" }));
    } finally {
        globalThis.fetch = prevFetch;
    }
});
