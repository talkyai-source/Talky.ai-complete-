import assert from "node:assert/strict";
import test from "node:test";

import { leadDetailsApi, type CampaignLeadField } from "@/lib/lead-details-api";

test("campaign lead-field reads and writes use the campaign-scoped contract", async () => {
    const originalFetch = globalThis.fetch;
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    const fields: CampaignLeadField[] = [{
        field_key: "company_name",
        label: "Company",
        field_type: "text",
        is_required: true,
        agent_visible: true,
        user_visible: true,
        options: null,
        sort_order: 0,
    }];
    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(url), init });
        return new Response(JSON.stringify({ fields }), {
            status: 200,
            headers: { "content-type": "application/json" },
        });
    }) as typeof fetch;

    try {
        await leadDetailsApi.campaignFields("campaign/unsafe");
        await leadDetailsApi.setCampaignFields("campaign/unsafe", fields);

        assert.match(calls[0]!.url, /\/campaigns\/campaign%2Funsafe\/lead-fields$/);
        assert.equal(calls[0]!.init?.method, "GET");
        assert.equal(calls[1]!.init?.method, "PUT");
        assert.deepEqual(JSON.parse(String(calls[1]!.init?.body)), fields);
    } finally {
        globalThis.fetch = originalFetch;
    }
});
