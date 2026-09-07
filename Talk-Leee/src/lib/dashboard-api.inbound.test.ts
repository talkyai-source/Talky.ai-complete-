import assert from "node:assert/strict";
import { test } from "node:test";

test("inbound call list and detail preserve direction and caller/DID parties", async () => {
    const previousFetch = globalThis.fetch;
    const calls: string[] = [];
    let includeInboundCampaignId = true;
    process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1";
    const inbound = {
        id: "call-in-1",
        timestamp: "2026-08-26T10:00:00Z",
        from_number: "+14155550111",
        to_number: "+14155550222",
        status: "completed",
        direction: "inbound",
        campaign_id: "agent-1",
        campaign_name: "Reception",
        inbound_campaign_id: "inbound-1",
    };
    globalThis.fetch = (async (url: RequestInfo | URL) => {
        const value = String(url);
        calls.push(value);
        const detail = includeInboundCampaignId ? inbound : { ...inbound, inbound_campaign_id: undefined };
        const body = value.includes("/calls/call-in-1") ? detail : { items: [inbound], total: 1 };
        return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
    }) as typeof fetch;

    try {
        const { dashboardApi } = await import("@/lib/dashboard-api");
        const list = await dashboardApi.listCalls(1, 20, { direction: "inbound", inboundCampaignId: "inbound-1" });
        assert.match(calls[0] ?? "", /direction=inbound/);
        assert.match(calls[0] ?? "", /inbound_campaign_id=inbound-1/);
        assert.equal(list.calls[0]?.direction, "inbound");
        assert.equal(list.calls[0]?.phone_number, inbound.from_number);
        assert.equal(list.calls[0]?.from_number, inbound.from_number);
        assert.equal(list.calls[0]?.to_number, inbound.to_number);

        const detail = await dashboardApi.getCall(inbound.id);
        assert.equal(detail.direction, "inbound");
        assert.equal(detail.phone_number, inbound.from_number);
        assert.equal(detail.from_number, inbound.from_number);
        assert.equal(detail.to_number, inbound.to_number);
        assert.equal(detail.inbound_campaign_id, inbound.inbound_campaign_id);

        includeInboundCampaignId = false;
        const detailWithoutRouteContract = await dashboardApi.getCall(inbound.id);
        assert.equal(detailWithoutRouteContract.inbound_campaign_id, undefined);
        assert.notEqual(detailWithoutRouteContract.inbound_campaign_id, detailWithoutRouteContract.campaign_id);
    } finally {
        globalThis.fetch = previousFetch;
    }
});

test("call detail passes the server's transfer legs through untouched", async () => {
    const previousFetch = globalThis.fetch;
    process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1";
    // Key names are the server's own SELECT list —
    // backend/app/api/v1/endpoints/calls.py:1329-1337.
    const legs = [
        {
            id: "leg-1",
            leg_type: "transfer",
            status: "failed",
            to_number: "+14155550199",
            duration_seconds: 0,
            metadata: { terminal_reason: "provider_target_absent" },
        },
    ];
    globalThis.fetch = (async () => new Response(
        JSON.stringify({
            id: "call-t-1",
            timestamp: "2026-09-02T10:00:00Z",
            to_number: "+14155550222",
            status: "completed",
            direction: "inbound",
            transfer_legs: legs,
        }),
        { status: 200, headers: { "content-type": "application/json" } },
    )) as typeof fetch;

    try {
        const { dashboardApi } = await import("@/lib/dashboard-api");
        const detail = await dashboardApi.getCall("call-t-1");
        assert.deepEqual(detail.transfer_legs, legs);
        assert.equal(detail.transfer_legs?.[0]?.status, "failed");
    } finally {
        globalThis.fetch = previousFetch;
    }
});

test("call detail leaves transfer legs undefined when the server sends none", async () => {
    const previousFetch = globalThis.fetch;
    process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1";
    let body: Record<string, unknown> = {};
    globalThis.fetch = (async () => new Response(
        JSON.stringify({
            id: "call-t-2",
            timestamp: "2026-09-02T10:00:00Z",
            to_number: "+14155550222",
            status: "completed",
            direction: "inbound",
            ...body,
        }),
        { status: 200, headers: { "content-type": "application/json" } },
    )) as typeof fetch;

    try {
        const { dashboardApi } = await import("@/lib/dashboard-api");
        // Field absent entirely (older server): undefined, not an invented list.
        assert.equal((await dashboardApi.getCall("call-t-2")).transfer_legs, undefined);

        // Explicit null must not become an array either.
        body = { transfer_legs: null };
        assert.equal((await dashboardApi.getCall("call-t-2")).transfer_legs, undefined);

        // An empty array is the server saying "no legs" and is preserved as
        // such — distinguishable from "the server said nothing".
        body = { transfer_legs: [] };
        assert.deepEqual((await dashboardApi.getCall("call-t-2")).transfer_legs, []);
    } finally {
        globalThis.fetch = previousFetch;
    }
});

test("transferLegTerminalReason reads the server's reason and returns null otherwise", async () => {
    const { transferLegTerminalReason } = await import("@/lib/dashboard-api");
    // backend/app/domain/services/telephony/inbound_transfer.py:1410-1435
    assert.equal(
        transferLegTerminalReason({ metadata: { terminal_reason: "parent_already_terminal" } }),
        "parent_already_terminal",
    );
    assert.equal(transferLegTerminalReason({ metadata: { terminal_reason: "  spaced  " } }), "spaced");
    assert.equal(transferLegTerminalReason({ metadata: { terminal_reason: "   " } }), null);
    assert.equal(transferLegTerminalReason({ metadata: { terminal_reason: 42 } }), null);
    assert.equal(transferLegTerminalReason({ metadata: {} }), null);
    assert.equal(transferLegTerminalReason({ metadata: null }), null);
    assert.equal(transferLegTerminalReason({}), null);
});

test("call detail carries the server's billed seconds through untouched", async () => {
    const previousFetch = globalThis.fetch;
    process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1";
    // billed_duration_seconds is projected off the immutable usage ledger and
    // is deliberately unrelated to duration_seconds —
    // backend/app/api/v1/endpoints/calls.py:263, :1306-1312.
    let billed: number | null | undefined = 1830;
    globalThis.fetch = (async () =>
        new Response(
            JSON.stringify({
                id: "call-in-2",
                timestamp: "2026-09-02T10:00:00Z",
                to_number: "+14155550222",
                status: "completed",
                direction: "inbound",
                duration_seconds: 1841,
                billing_status: "finalized",
                billed_duration_seconds: billed,
            }),
            { status: 200, headers: { "content-type": "application/json" } },
        )) as typeof fetch;

    try {
        const { dashboardApi } = await import("@/lib/dashboard-api");
        const settled = await dashboardApi.getCall("call-in-2");
        assert.equal(settled.billed_duration_seconds, 1830);
        assert.equal(settled.billing_status, "finalized");
        // The billed figure is never the duration, and is never derived from it.
        assert.notEqual(settled.billed_duration_seconds, settled.duration_seconds);

        // An unsettled call sends null; that must stay distinguishable from a
        // billed zero rather than collapsing into one.
        billed = null;
        const unsettled = await dashboardApi.getCall("call-in-2");
        assert.equal(unsettled.billed_duration_seconds, undefined);
        assert.equal(unsettled.duration_seconds, 1841);

        billed = 0;
        const released = await dashboardApi.getCall("call-in-2");
        assert.equal(released.billed_duration_seconds, 0);

        // An older server sends no such key at all.
        billed = undefined;
        const legacy = await dashboardApi.getCall("call-in-2");
        assert.equal(legacy.billed_duration_seconds, undefined);
    } finally {
        globalThis.fetch = previousFetch;
    }
});
