import { test } from "node:test";
import assert from "node:assert/strict";
import {
    applyCampaignFilters,
    applyCampaignSort,
    campaignProgressPct,
    campaignSuccessRatePct,
    groupEventTime,
    normalizeCampaignStatus,
    paginate,
    parseCommandInput,
} from "@/lib/campaign-performance";
import type { Campaign } from "@/lib/dashboard-api";

function makeCampaign(overrides: Partial<Campaign>): Campaign {
    return {
        id: overrides.id ?? "camp-x",
        name: overrides.name ?? "Campaign",
        description: overrides.description,
        status: overrides.status ?? "draft",
        system_prompt: overrides.system_prompt ?? "",
        voice_id: overrides.voice_id ?? "voice",
        max_concurrent_calls: overrides.max_concurrent_calls ?? 10,
        total_leads: overrides.total_leads ?? 0,
        calls_completed: overrides.calls_completed ?? 0,
        calls_failed: overrides.calls_failed ?? 0,
        created_at: overrides.created_at ?? new Date("2025-01-01T00:00:00Z").toISOString(),
        started_at: overrides.started_at,
        completed_at: overrides.completed_at,
    };
}

test("normalizeCampaignStatus maps known statuses", () => {
    assert.equal(normalizeCampaignStatus("running"), "Active");
    assert.equal(normalizeCampaignStatus("paused"), "Paused");
    assert.equal(normalizeCampaignStatus("completed"), "Completed");
    assert.equal(normalizeCampaignStatus("draft"), "Draft");
    assert.equal(normalizeCampaignStatus("failed"), "Failed");
    assert.equal(normalizeCampaignStatus("stopped"), "Failed");
});

test("campaignProgressPct clamps and handles zero leads", () => {
    assert.equal(campaignProgressPct(makeCampaign({ total_leads: 0, calls_completed: 10 })), 0);
    assert.equal(campaignProgressPct(makeCampaign({ total_leads: 100, calls_completed: 10 })), 10);
    assert.equal(campaignProgressPct(makeCampaign({ total_leads: 100, calls_completed: 999 })), 100);
});

test("campaignSuccessRatePct uses completed / (completed+failed)", () => {
    assert.equal(campaignSuccessRatePct(makeCampaign({ calls_completed: 0, calls_failed: 0 })), 0);
    assert.equal(campaignSuccessRatePct(makeCampaign({ calls_completed: 10, calls_failed: 0 })), 100);
    assert.equal(campaignSuccessRatePct(makeCampaign({ calls_completed: 10, calls_failed: 10 })), 50);
});

test("applyCampaignFilters supports status, success range, and query", () => {
    const items = [
        makeCampaign({ id: "a", name: "Holiday Sales", status: "running", calls_completed: 10, calls_failed: 0 }),
        makeCampaign({ id: "b", name: "Survey", status: "paused", calls_completed: 5, calls_failed: 5 }),
        makeCampaign({ id: "c", name: "Draft X", status: "draft", calls_completed: 0, calls_failed: 0 }),
    ];

    const filtered = applyCampaignFilters(items, { statuses: ["Active"], successMin: 0, successMax: 100, query: "" });
    assert.deepEqual(filtered.map((c) => c.id), ["a"]);

    const filtered2 = applyCampaignFilters(items, { statuses: [], successMin: 60, successMax: 100, query: "" });
    assert.deepEqual(filtered2.map((c) => c.id), ["a"]);

    const filtered3 = applyCampaignFilters(items, { statuses: [], successMin: 0, successMax: 100, query: "holi" });
    assert.deepEqual(filtered3.map((c) => c.id), ["a"]);
});

test("applyCampaignSort supports multi-column and stable tiebreak", () => {
    const items = [
        makeCampaign({ id: "a", name: "Alpha", total_leads: 100, calls_completed: 10 }),
        makeCampaign({ id: "b", name: "Alpha", total_leads: 100, calls_completed: 20 }),
        makeCampaign({ id: "c", name: "Bravo", total_leads: 50, calls_completed: 5 }),
    ];

    const sorted = applyCampaignSort(items, [
        { key: "name", dir: "asc" },
        { key: "completed", dir: "desc" },
    ]);
    assert.deepEqual(sorted.map((c) => c.id), ["b", "a", "c"]);
});

test("paginate returns correct range and pageCount", () => {
    const items = Array.from({ length: 143 }).map((_, i) => i + 1);
    const p = paginate(items, 2, 25);
    assert.equal(p.pageCount, 6);
    assert.equal(p.start, 26);
    assert.equal(p.end, 50);
    assert.equal(p.slice.length, 25);
});

test("parseCommandInput reads prefixes and query", () => {
    assert.deepEqual(parseCommandInput("  /pause"), { prefix: "/", query: "pause" });
    assert.deepEqual(parseCommandInput("> analytics"), { prefix: ">", query: "analytics" });
    assert.deepEqual(parseCommandInput("@ alex"), { prefix: "@", query: "alex" });
    assert.deepEqual(parseCommandInput("# priority"), { prefix: "#", query: "priority" });
    assert.deepEqual(parseCommandInput("Holiday"), { prefix: "", query: "Holiday" });
});

test("groupEventTime buckets dates into groups", () => {
    const now = new Date("2026-01-09T12:00:00.000Z");
    assert.equal(groupEventTime("2026-01-09T01:00:00.000Z", now), "Today");
    assert.equal(groupEventTime("2026-01-08T23:59:00.000Z", now), "Yesterday");
    assert.equal(groupEventTime("2026-01-04T12:00:00.000Z", now), "Last 7 Days");
    assert.equal(groupEventTime("2025-12-01T00:00:00.000Z", now), "Older");
});

