import assert from "node:assert/strict";
import { test } from "node:test";

test("backendApi.voiceCalls.guard posts guarded call payload and parses allow response", async () => {
    const prevFetch = globalThis.fetch;
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(url), init });
        return new Response(
            JSON.stringify({
                outcome: "ALLOW",
                tenant_id: "tenant-a",
                partner_id: "acme",
                reservation_id: "res-1",
                active_calls: { tenant: 1, partner: 2 },
                overage: { tenant: false, partner: false },
                allowed_features: ["voice"],
                requested_features: ["voice"],
                usage_account_id: "usage-1",
                billing_account_id: "billing-1",
            }),
            { status: 200, headers: { "content-type": "application/json" } }
        );
    }) as typeof fetch;

    try {
        process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1";
        const { backendApi } = await import("@/lib/backend-api");
        const res = await backendApi.voiceCalls.guard({
            tenantId: "tenant-a",
            partnerId: "acme",
            requestedFeatures: ["voice"],
            callId: "call-1",
            providerCallId: "provider-1",
            allowOverage: true,
        });

        assert.equal(res.outcome, "ALLOW");
        assert.equal(res.tenantId, "tenant-a");
        assert.equal(calls.length, 1);
        assert.match(calls[0]!.url, /\/voice\/calls\/guard$/);
        assert.equal(calls[0]!.init?.method, "POST");
        const body = JSON.parse(String(calls[0]!.init?.body)) as Record<string, unknown>;
        assert.equal(body.tenant_id, "tenant-a");
        assert.equal(body.partner_id, "acme");
        assert.deepEqual(body.requested_features, ["voice"]);
        assert.equal(body.call_id, "call-1");
        assert.equal(body.provider_call_id, "provider-1");
        assert.equal(body.allow_overage, true);
    } finally {
        globalThis.fetch = prevFetch;
    }
});

test("backendApi.voiceCalls.start posts start payload and parses active session response", async () => {
    const prevFetch = globalThis.fetch;
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(url), init });
        return new Response(
            JSON.stringify({
                outcome: "ALLOW",
                tenant_id: "tenant-a",
                partner_id: "acme",
                reservation_id: "res-1",
                call_id: "call-1",
                provider_call_id: "provider-1",
                status: "active",
                started_at: "2026-01-14T10:00:00.000Z",
                active_calls: { tenant: 1, partner: 2 },
                overage: { tenant: false, partner: false },
                allowed_features: ["voice"],
                requested_features: ["voice"],
                usage_account_id: "usage-1",
                billing_account_id: "billing-1",
            }),
            { status: 201, headers: { "content-type": "application/json" } }
        );
    }) as typeof fetch;

    try {
        process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1";
        const { backendApi } = await import("@/lib/backend-api");
        const res = await backendApi.voiceCalls.start({
            requestedFeatures: ["voice"],
            providerCallId: "provider-1",
        });

        assert.equal(res.outcome, "ALLOW");
        assert.equal(res.status, "active");
        assert.equal(res.callId, "call-1");
        assert.equal(calls.length, 1);
        assert.match(calls[0]!.url, /\/voice\/calls\/start$/);
        assert.equal(calls[0]!.init?.method, "POST");
        const body = JSON.parse(String(calls[0]!.init?.body)) as Record<string, unknown>;
        assert.deepEqual(body.requested_features, ["voice"]);
        assert.equal(body.provider_call_id, "provider-1");
    } finally {
        globalThis.fetch = prevFetch;
    }
});
