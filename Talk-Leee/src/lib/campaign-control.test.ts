import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";

import {
    beginCampaignAction,
    campaignStartLabel,
    campaignsInFlight,
    canStartCampaign,
    contactsQueuedText,
    endCampaignAction,
    notPausedText,
    notStartedText,
    runCampaignPause,
    runCampaignPauses,
    runCampaignStarts,
    runCampaignStop,
    startOutcomeNotice,
    terminationNoticeText,
} from "@/lib/campaign-control";
import { dashboardApi, type CampaignControlResponse, type CampaignStartOptions } from "@/lib/dashboard-api";

afterEach(() => {
    mock.restoreAll();
    for (const kind of ["starting", "stopping", "pausing"] as const) {
        for (const id of Array.from(campaignsInFlight()[kind])) endCampaignAction(kind, id);
    }
});

function control(summary: Record<string, unknown>): CampaignControlResponse {
    return { message: "Campaign 11111111-2222-3333-4444-555555555555 stopped", campaign: {}, termination_summary: summary } as unknown as CampaignControlResponse;
}

test("Start / Resume is available for exactly draft, paused and stopped", () => {
    assert.deepEqual(
        ["draft", "paused", "stopped", "running", "completed", "scheduled", "cancelled", "failed", ""].map((s) => canStartCampaign(s)),
        [true, true, true, false, false, false, false, false, false],
    );
});

test("the button reads Start for draft and stopped, Resume for paused", () => {
    assert.equal(campaignStartLabel("draft"), "Start");
    assert.equal(campaignStartLabel("stopped"), "Start");
    assert.equal(campaignStartLabel("paused"), "Resume");
});

test("the result texts are exact", () => {
    assert.equal(contactsQueuedText(42), "42 contacts queued for calling.");
    assert.equal(notStartedText("Spring push", "Selected trunk unavailable"), "Spring push: not started — Selected trunk unavailable");
    assert.equal(notPausedText("Spring push"), "Spring push: not paused — not running");
    assert.equal(terminationNoticeText({ status: "confirmed" }), "All live calls were ended.");
    assert.equal(terminationNoticeText({ status: "partial", deferred: 3 }), "3 live calls are still ending.");
    assert.equal(terminationNoticeText({ status: "partial", unconfirmed: 2 }), "2 live calls are still ending.");
    assert.equal(terminationNoticeText({ status: "lookup_failed" }), "Live calls could not be checked. Some may still be ending.");
    assert.equal(terminationNoticeText(undefined), "Live calls could not be checked. Some may still be ending.");
});

test("a second start of the same campaign is ignored while the first is running", async () => {
    const sent: Array<{ id: string; options: CampaignStartOptions }> = [];
    let finish!: (value: { message: string; jobs_enqueued: number }) => void;
    // Only the first request is held open; a second one (the bug) would answer at once.
    mock.method(dashboardApi, "startCampaign", (id: string, options: CampaignStartOptions) => {
        sent.push({ id, options });
        if (sent.length > 1) return Promise.resolve({ message: "ok", jobs_enqueued: 99 });
        return new Promise((resolve) => { finish = resolve; });
    });
    const first = runCampaignStarts([{ id: "c1", name: "One" }], { first_speaker: "user" });
    assert.equal(campaignsInFlight().starting.has("c1"), true);
    const second = await runCampaignStarts([{ id: "c1", name: "One" }], { first_speaker: "agent" });
    assert.deepEqual(second, { queued: null, startedIds: [], notStarted: [] }, "the second start did nothing");
    assert.equal(sent.length, 1, "one POST only");
    finish({ message: "ok", jobs_enqueued: 7 });
    assert.deepEqual(await first, { queued: 7, startedIds: ["c1"], notStarted: [] });
    assert.equal(campaignsInFlight().starting.has("c1"), false);
    assert.deepEqual(sent[0], { id: "c1", options: { first_speaker: "user" } });
});

test("a start keeps the campaign in flight until the caller's reload has finished", async () => {
    mock.method(dashboardApi, "startCampaign", async () => ({ message: "ok", jobs_enqueued: 2 }));
    let seenDuringReload: boolean | null = null;
    await runCampaignStarts([{ id: "c1", name: "One" }], { first_speaker: "agent" }, async () => {
        seenDuringReload = campaignsInFlight().starting.has("c1");
    });
    assert.equal(seenDuringReload, true);
    assert.equal(campaignsInFlight().starting.has("c1"), false);
});

test("several campaigns: the queued counts are added and a refused one is listed with the server's reason", async () => {
    mock.method(dashboardApi, "startCampaign", async (id: string) => {
        if (id === "c2") throw Object.assign(new Error("Payment Required"), { details: { message: "You have no minutes left." } });
        return { message: "ok", jobs_enqueued: id === "c1" ? 5 : 8 };
    });
    const outcome = await runCampaignStarts(
        [{ id: "c1", name: "One" }, { id: "c2", name: "Two" }, { id: "c3", name: "Three" }],
        { first_speaker: "agent" },
    );
    assert.deepEqual(outcome, { queued: 13, startedIds: ["c1", "c3"], notStarted: [{ id: "c2", name: "Two", reason: "You have no minutes left." }] });
    assert.deepEqual(startOutcomeNotice(outcome), {
        kind: "warning",
        lines: ["13 contacts queued for calling.", "Two: not started — You have no minutes left."],
    });
});

test("a stop returns the notice for its termination summary and never the campaign id", async () => {
    const summaries: Array<[Record<string, unknown>, string, string]> = [
        [{ status: "confirmed" }, "success", "All live calls were ended."],
        [{ status: "partial", deferred: 4 }, "warning", "4 live calls are still ending."],
        [{ status: "lookup_failed" }, "warning", "Live calls could not be checked. Some may still be ending."],
    ];
    for (const [summary, kind, text] of summaries) {
        mock.method(dashboardApi, "stopCampaign", async () => control(summary));
        const notice = await runCampaignStop("11111111-2222-3333-4444-555555555555");
        assert.deepEqual(notice, { kind, lines: [text] });
        assert.equal(notice!.lines.join(" ").includes("11111111"), false);
        mock.restoreAll();
    }
});

test("a stop already running is ignored, and a refused stop reports the server's message", async () => {
    let calls = 0;
    mock.method(dashboardApi, "stopCampaign", async () => { calls++; throw new Error("Campaign is not running"); });
    assert.equal(beginCampaignAction("stopping", "c1"), true);
    assert.equal(await runCampaignStop("c1"), null);
    assert.equal(calls, 0);
    endCampaignAction("stopping", "c1");
    assert.deepEqual(await runCampaignStop("c1"), { kind: "error", lines: ["Campaign is not running"] });
    assert.equal(campaignsInFlight().stopping.has("c1"), false);
});

test("a pause reads as in flight while its request runs", async () => {
    let finish!: (value: CampaignControlResponse) => void;
    mock.method(dashboardApi, "pauseCampaign", () => new Promise((resolve) => { finish = resolve; }));
    const pending = runCampaignPause("c1");
    assert.equal(campaignsInFlight().pausing.has("c1"), true);
    finish(control({ status: "confirmed" }));
    assert.deepEqual(await pending, { kind: "success", lines: ["All live calls were ended."] });
    assert.equal(campaignsInFlight().pausing.has("c1"), false);
});

test("pausing a set acts only on running campaigns and lists the others", async () => {
    const paused: string[] = [];
    mock.method(dashboardApi, "pauseCampaign", async (id: string) => { paused.push(id); return control({ status: "confirmed" }); });
    const notice = await runCampaignPauses([
        { id: "c1", name: "One", status: "running" },
        { id: "c2", name: "Two", status: "paused" },
        { id: "c3", name: "Three", status: "draft" },
        { id: "c4", name: "Four", status: "running" },
    ]);
    assert.deepEqual(paused, ["c1", "c4"]);
    assert.deepEqual(notice, {
        kind: "warning",
        lines: ["All live calls were ended.", "Two: not paused — not running", "Three: not paused — not running"],
    });
});

test("pausing a set adds up the calls still ending and reports an unchecked cleanup", async () => {
    mock.method(dashboardApi, "pauseCampaign", async (id: string) => control(
        id === "c1" ? { status: "partial", deferred: 2 } : id === "c2" ? { status: "partial", deferred: 3 } : { status: "lookup_failed" },
    ));
    const notice = await runCampaignPauses([
        { id: "c1", name: "One", status: "running" },
        { id: "c2", name: "Two", status: "running" },
        { id: "c3", name: "Three", status: "running" },
    ]);
    assert.deepEqual(notice, {
        kind: "warning",
        lines: ["5 live calls are still ending.", "Live calls could not be checked. Some may still be ending."],
    });
});

// ── FX3a Task 2.6 ────────────────────────────────────────────────────────────
test("FX3a: one live call still ending is singular", () => {
    assert.equal(terminationNoticeText({ status: "partial", deferred: 1 }), "1 live call is still ending.");
    assert.equal(terminationNoticeText({ status: "partial", deferred: 1 }).includes("1 live calls"), false);
    assert.equal(terminationNoticeText({ status: "partial", deferred: 2 }), "2 live calls are still ending.");
});
