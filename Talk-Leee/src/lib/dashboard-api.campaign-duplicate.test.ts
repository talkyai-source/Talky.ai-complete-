import assert from "node:assert/strict";
import { test } from "node:test";
import { dashboardApi, type Campaign } from "@/lib/dashboard-api";

const SOURCE = "ddeeffaa-1234-4abc-8def-0123456789ab";
const COPY = "abcdefff-1234-4abc-8def-0123456789ab";
// The missing method is the pre-fix runtime defect, not a TypeScript collection error.
const api = dashboardApi as typeof dashboardApi & {
    duplicateCampaign: (id: string) => Promise<{ campaign: Campaign }>;
};

test("duplicateCampaign posts to the durable route and returns its campaign", async () => {
    const original = globalThis.fetch;
    const requests: { url: string; method: string | undefined }[] = [];
    const campaign = { id: COPY, name: "Persisted copy", status: "draft", total_leads: 0 };
    globalThis.fetch = (async (input, init) => {
        requests.push({ url: String(input), method: init?.method });
        return new Response(JSON.stringify({ campaign }), { status: 201,
            headers: { "Content-Type": "application/json" } });
    }) as typeof fetch;
    try {
        const result = await api.duplicateCampaign(SOURCE);
        assert.deepEqual(result.campaign, campaign);
        assert.equal(requests.length, 1);
        assert.equal(new URL(requests[0].url).pathname, `/api/v1/campaigns/${SOURCE}/duplicate`);
        assert.equal(requests[0].method, "POST");
    } finally {
        globalThis.fetch = original;
    }
});

for (const [label, body] of [["null", { campaign: null }], ["missing", {}], ["no-id", { campaign: {} }]] as const) {
    test(`duplicateCampaign refuses a successful response with ${label} campaign identity`, async () => {
        const original = globalThis.fetch;
        globalThis.fetch = (async () => new Response(JSON.stringify(body), { status: 201,
            headers: { "Content-Type": "application/json" } })) as typeof fetch;
        try {
            await assert.rejects(api.duplicateCampaign(SOURCE), /backend did not return a duplicated campaign/i);
        } finally {
            globalThis.fetch = original;
        }
    });
}
