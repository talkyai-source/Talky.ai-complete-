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
