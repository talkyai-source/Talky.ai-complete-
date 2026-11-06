import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import { dashboardApi } from "@/lib/dashboard-api";
import { extendedApi } from "@/lib/extended-api";
import * as reportingTime from "@/lib/reporting-time";

let urls: URL[] = [];
beforeEach(() => {
    urls = [];
    mock.method(globalThis, "fetch", async (url: RequestInfo | URL) => {
        urls.push(new URL(String(url)));
        return new Response(JSON.stringify({ timezone: "Asia/Karachi", series: [], campaigns: [], hours: [], attempts: [] }),
            { status: 200, headers: { "content-type": "application/json" } });
    });
});
afterEach(() => mock.restoreAll());

for (const [name, operation, receiver, args] of [
    ["call series", extendedApi.getCallAnalytics, extendedApi, ["2026-11-01", "2026-11-01", "day", "outbound", false, "Asia/Karachi"]],
    ["campaign series", extendedApi.getCallAnalyticsByCampaign, extendedApi, ["2026-11-01", "2026-11-01", "hour", "outbound", false, "Asia/Karachi"]],
    ["dashboard summary", dashboardApi.getDashboardSummary, dashboardApi, ["Asia/Karachi"]],
] as const) {
    test(`${name} sends an explicitly chosen reporting timezone through the real HTTP client`, async () => {
        const result = await Reflect.apply(operation, receiver, [...args]);
        assert.equal(urls.length, 1);
        assert.equal(urls[0].searchParams.get("tz"), "Asia/Karachi");
        assert.equal(result.timezone, "Asia/Karachi");
        if (name !== "dashboard summary") {
            assert.equal(urls[0].searchParams.get("from"), "2026-11-01");
            assert.equal(urls[0].searchParams.get("to"), "2026-11-01");
            assert.equal(urls[0].searchParams.get("direction"), "outbound");
            assert.equal(urls[0].searchParams.get("include_tests"), "false");
        }
    });
}

test("omitted reporting timezone stays omitted so each backend route resolves stored defaults", async () => {
    await extendedApi.getCallAnalytics();
    await extendedApi.getCallAnalyticsByCampaign();
    await extendedApi.getBestTimeToCall();
    await extendedApi.getRetryEffectiveness();
    await dashboardApi.getDashboardSummary();
    assert.equal(urls.length, 5);
    assert.ok(urls.every((url) => !url.searchParams.has("tz")));
});

for (const [day, start, end] of [
    ["2026-03-08", "2026-03-08T05:00:00Z", "2026-03-09T04:00:00Z"],
    ["2026-11-01", "2026-11-01T04:00:00Z", "2026-11-02T05:00:00Z"],
] as const) {
    test(`daily chart ${day} uses actual DST-aware bucket endpoints`, () => {
        const operation = Reflect.get(reportingTime, "calendarBucketBounds");
        assert.equal(typeof operation, "function");
        assert.deepEqual(operation(day, "America/New_York"), { startMs: Date.parse(start), endMs: Date.parse(end) });
    });
}
