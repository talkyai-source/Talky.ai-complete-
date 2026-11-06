import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { dashboardApi } from "./dashboard-api";
import { setRequestIdentity, setTokenProvider } from "./http-client";

const originalFetch = globalThis.fetch;
afterEach(() => {
    globalThis.fetch = originalFetch;
    setTokenProvider(null);
    setRequestIdentity(null);
});

for (const firstSpeaker of [undefined, "user", "agent"] as const) {
    test(`campaign resume sends only an explicit speaker override: ${firstSpeaker}`, async () => {
        let body: Record<string, unknown> | undefined;
        setTokenProvider(() => "synthetic-token");
        setRequestIdentity({ userId: "user-a", tenantId: "tenant-a" });
        globalThis.fetch = (async (_url, init) => {
            body = JSON.parse(String(init?.body));
            return Response.json({ message: "Started", jobs_enqueued: 1 });
        }) as typeof fetch;
        await dashboardApi.startCampaign("synthetic-campaign", firstSpeaker ? { first_speaker: firstSpeaker } : undefined);
        assert.deepEqual(body, firstSpeaker ? { first_speaker: firstSpeaker } : {});
    });
}
