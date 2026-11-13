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

// Plan 3.3 (merged 11 Nov 2026): the speaker is not saved on the campaign, so
// the shared Start dialog's choice goes with every start and resume.
for (const firstSpeaker of ["user", "agent"] as const) {
    test(`campaign start sends the chosen first speaker: ${firstSpeaker}`, async () => {
        let body: Record<string, unknown> | undefined;
        setTokenProvider(() => "synthetic-token");
        setRequestIdentity({ userId: "user-a", tenantId: "tenant-a" });
        globalThis.fetch = (async (_url, init) => {
            body = JSON.parse(String(init?.body));
            return Response.json({ message: "Started", jobs_enqueued: 1 });
        }) as typeof fetch;
        await dashboardApi.startCampaign("synthetic-campaign", { first_speaker: firstSpeaker });
        assert.deepEqual(body, { first_speaker: firstSpeaker });
    });
}
