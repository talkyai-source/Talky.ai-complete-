/**
 * FX6b Task 1 and the two shared helpers behind Tasks 2-4.
 * GET /campaigns/{id} answers {campaign, effective_schedule, progress}
 * (backend/app/api/v1/endpoints/campaigns.py); dashboardApi.getCampaign hands
 * all three on, and callers that read only `campaign` keep working.
 */
import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";

import { enforcedSchedule, formatCallingSchedule } from "@/lib/calling-schedule-text";
import { campaignDisplayStatus, isCampaignTransientPhase } from "@/lib/campaign-status";
import { dashboardApi, type CampaignEffectiveSchedule } from "@/lib/dashboard-api";

afterEach(() => mock.restoreAll());

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
const CAMPAIGN = { id: "c1", name: "Alpha", status: "running", direction: "outbound" };
const SCHEDULE: CampaignEffectiveSchedule = {
    timezone: "Europe/London", time_window_start: "08:00", time_window_end: "17:00", allowed_days: [0, 1],
    sources: { timezone: "campaign", time_window_start: "account", time_window_end: "account", allowed_days: "platform_default" },
    ignore_schedule_requested: false, ignore_schedule_allowed: true, ignore_schedule: false,
};

test("getCampaign hands on effective_schedule and progress with the campaign", async () => {
    const progress = { live_calls: 2, jobs_awaiting_dispatch: 5, phase: "preparing" };
    const fetched = mock.method(globalThis, "fetch", async () => json({ campaign: CAMPAIGN, effective_schedule: SCHEDULE, progress }));
    const answer = await dashboardApi.getCampaign("c1");
    assert.equal(new URL(String(fetched.mock.calls[0]!.arguments[0] instanceof Request ? (fetched.mock.calls[0]!.arguments[0] as Request).url : fetched.mock.calls[0]!.arguments[0])).pathname.endsWith("/campaigns/c1"), true);
    assert.equal(answer.campaign.id, "c1");
    assert.deepEqual(answer.effective_schedule, SCHEDULE);
    assert.deepEqual(answer.progress, progress);
});

test("getCampaign keeps both fields null when the server's side read failed, and callers that read only campaign still work", async () => {
    mock.method(globalThis, "fetch", async () => json({ campaign: CAMPAIGN, effective_schedule: null, progress: null }));
    const answer = await dashboardApi.getCampaign("c1");
    assert.equal(answer.effective_schedule, null);
    assert.equal(answer.progress, null);
    const { campaign } = await dashboardApi.getCampaign("c1");
    assert.equal(campaign.name, "Alpha");
});

test("enforcedSchedule: the server's hours in the saved-schedule shape, in the existing line format", () => {
    assert.deepEqual(enforcedSchedule(SCHEDULE), { timezone: "Europe/London", time_window_start: "08:00", time_window_end: "17:00", allowed_days: [0, 1] });
    assert.equal(formatCallingSchedule(enforcedSchedule(SCHEDULE)), "Europe/London · 08:00–17:00 · Mon, Tue");
});

test("enforcedSchedule: call anytime asked for but not allowed is still enforced", () => {
    const refused = { ...SCHEDULE, ignore_schedule_requested: true, ignore_schedule_allowed: false, ignore_schedule: false };
    assert.equal(enforcedSchedule(refused) !== null, true);
});

test("enforcedSchedule: null when there is none, when the dialer ignores the hours, or when a field is missing", () => {
    assert.equal(enforcedSchedule(null), null);
    assert.equal(enforcedSchedule(undefined), null);
    assert.equal(enforcedSchedule({ ...SCHEDULE, ignore_schedule_requested: true, ignore_schedule: true }), null);
    assert.equal(enforcedSchedule({ ...SCHEDULE, timezone: "" }), null);
    assert.equal(enforcedSchedule({ ...SCHEDULE, allowed_days: [] }), null);
});

test("campaignDisplayStatus: the server's phase names the status; pausing reads Paused", () => {
    assert.equal(campaignDisplayStatus("running", undefined, "preparing"), "Preparing");
    assert.equal(campaignDisplayStatus("stopped", undefined, "stopping"), "Stopping");
    assert.equal(campaignDisplayStatus("paused", undefined, "pausing"), "Paused");
});

test("campaignDisplayStatus: any other phase, or none, is the label of the stored status", () => {
    for (const [status, label] of [["draft", "Draft"], ["running", "Running"], ["paused", "Paused"], ["completed", "Completed"], ["stopped", "Stopped"]]) {
        assert.equal(campaignDisplayStatus(status, undefined, status), label);
        assert.equal(campaignDisplayStatus(status, undefined, null), label);
        assert.equal(campaignDisplayStatus(status), label);
    }
});

test("campaignDisplayStatus: the browser's own request in flight still reads first", () => {
    assert.equal(campaignDisplayStatus("paused", { starting: true }, "pausing"), "Preparing");
    assert.equal(campaignDisplayStatus("running", { stopping: true }, "preparing"), "Stopping");
});

test("isCampaignTransientPhase: only preparing, stopping and pausing", () => {
    assert.deepEqual(["preparing", "stopping", "pausing"].map(isCampaignTransientPhase), [true, true, true]);
    assert.deepEqual(["running", "paused", "stopped", "draft", "completed", "", null, undefined].map(isCampaignTransientPhase), [false, false, false, false, false, false, false, false]);
});
