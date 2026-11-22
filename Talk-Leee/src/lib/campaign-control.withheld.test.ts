import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";

import { campaignsInFlight, endCampaignAction, runCampaignStarts, startOutcomeNotice } from "@/lib/campaign-control";
import { dashboardApi } from "@/lib/dashboard-api";

afterEach(() => {
    mock.restoreAll();
    for (const id of Array.from(campaignsInFlight().starting)) endCampaignAction("starting", id);
});

const options = { first_speaker: "agent" } as never;

test("withheld contacts add a line and make the notice a warning", () => {
    const notice = startOutcomeNotice({ queued: 40, startedIds: ["a"], notStarted: [], withheld: 312 });
    assert.deepEqual(notice, {
        kind: "warning",
        lines: [
            "40 contacts queued for calling.",
            "312 contacts were not queued because no permission to call is recorded for them. Confirm permission on their contact list.",
        ],
    });
});

test("one withheld contact uses the singular sentence", () => {
    const notice = startOutcomeNotice({ queued: 0, startedIds: ["a"], notStarted: [], withheld: 1 });
    assert.equal(notice?.lines[1], "1 contact was not queued because no permission to call is recorded for it. Confirm permission on its contact list.");
    assert.equal(notice?.kind, "warning");
});

test("no withheld field (older server) gives the old notice exactly", () => {
    assert.deepEqual(startOutcomeNotice({ queued: 40, startedIds: ["a"], notStarted: [] }), { kind: "success", lines: ["40 contacts queued for calling."] });
    assert.deepEqual(startOutcomeNotice({ queued: 40, startedIds: ["a"], notStarted: [], withheld: 0 }), { kind: "success", lines: ["40 contacts queued for calling."] });
});

test("runCampaignStarts sums jobs_withheld and leaves it absent when no answer carries it", async () => {
    mock.method(dashboardApi, "startCampaign", async (id: string) =>
        id === "a" ? { message: "ok", jobs_enqueued: 3, jobs_withheld: 2 } : { message: "ok", jobs_enqueued: 1, jobs_withheld: 4 });
    const both = await runCampaignStarts([{ id: "a", name: "A" }, { id: "b", name: "B" }], options);
    assert.equal(both.withheld, 6);
    assert.equal(both.queued, 4);
    mock.restoreAll();
    mock.method(dashboardApi, "startCampaign", async () => ({ message: "ok", jobs_enqueued: 3 }));
    const old = await runCampaignStarts([{ id: "c", name: "C" }], options);
    assert.equal(old.withheld, undefined);
    assert.deepEqual(startOutcomeNotice(old), { kind: "success", lines: ["3 contacts queued for calling."] });
});
