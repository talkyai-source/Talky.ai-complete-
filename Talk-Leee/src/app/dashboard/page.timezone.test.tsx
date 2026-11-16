import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { dashboardApi, type Campaign } from "@/lib/dashboard-api";
import { extendedApi } from "@/lib/extended-api";

// Plan 3.7 Z3 on the Dashboard: every date-sensitive figure says which zone
// it really uses. Every read (summary, daily, hourly, per campaign) is cut in
// the account's reporting zone, which the summary reports (QA F1-30/F1-32), so
// that is the zone all seven labels name, never UTC or the browser's, and a
// server day label prints as that same calendar date. The real page with
// the layout and the panels that fetch on their own stubbed; the chart stubs
// print the labels the page hands them.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/calls/call-issues-banner")) return { CallIssuesBanner: nothing };
    if (id.includes("components/dashboard/dialer-insights")) return { DialerInsights: nothing };
    if (id.includes("components/dashboard/first-call-setup")) return { FirstCallSetup: nothing };
    if (id.includes("components/ui/dashboard-charts")) return {
        ActivityFeed: nothing,
        AlertTimeline: ({ items }: { items: Array<{ id: string; timeLabel: string }> }) => (
            <ul data-testid="timeline-labels">{items.map((item) => <li key={item.id}>{item.timeLabel}</li>)}</ul>
        ),
        StackedAreaChart: ({ points }: { points: Array<{ label: string }> }) => (
            <ul data-testid="stacked-labels">{points.map((point) => <li key={point.label}>{point.label}</li>)}</ul>
        ),
        DonutChart: nothing, Heatmap: nothing, LiveCallsTimeSeriesChart: nothing,
    };
    return originalLoad.call(this, id, ...args);
};
let Page: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Page = require("./page").default;
} finally {
    loader._load = originalLoad;
}

const SLOW = 15_000;
// The hover tooltip positions itself on animation frames, which jsdom lacks.
const frames = window as unknown as { requestAnimationFrame?: (cb: FrameRequestCallback) => number; cancelAnimationFrame?: (id: number) => void };
if (typeof frames.requestAnimationFrame !== "function") {
    frames.requestAnimationFrame = (cb) => setTimeout(() => cb(performance.now()), 50) as unknown as number;
    frames.cancelAnimationFrame = (id) => clearTimeout(id);
}
const RealWebSocket = globalThis.WebSocket;
const tzBefore = process.env.TZ;
const router = { push() {}, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } as unknown as React.ContextType<typeof AppRouterContext>;

afterEach(() => {
    cleanup();
    mock.restoreAll();
    globalThis.WebSocket = RealWebSocket;
    if (tzBefore === undefined) delete process.env.TZ;
    else process.env.TZ = tzBefore;
});

const DAY = { date: "2026-03-01", total_calls: 4, answered: 3, failed: 1 };
// The account's reporting zone: neither UTC nor either browser zone used below.
const REPORTING_ZONE = "Asia/Karachi";

async function mountPage(day = DAY, hourRows = true) {
    globalThis.WebSocket = class { constructor() { throw new Error("no socket in tests"); } } as unknown as typeof WebSocket;
    const campaigns = [{
        id: "c0", name: "Running one", status: "running", direction: "outbound", system_prompt: "", voice_id: "v", max_concurrent_calls: 1,
        total_leads: 10, calls_completed: 3, calls_failed: 1, created_at: "2026-01-01T00:00:00Z",
    }] as unknown as Campaign[];
    mock.method(dashboardApi, "getDashboardSummary", async () => ({
        total_calls: 0, answered_calls: 0, failed_calls: 0, minutes_used: 0, minutes_remaining: 100, active_campaigns: 1,
        timezone: REPORTING_ZONE,
    }));
    mock.method(dashboardApi, "listCampaigns", async () => ({ campaigns }));
    // The hourly read answers with hour rows ("2026-03-01T00:00"), like the server.
    mock.method(extendedApi, "getCallAnalytics", async (_from: string, _to: string, group?: string) => ({
        series: group === "hour" ? (hourRows ? [{ ...day, date: `${day.date}T00:00` }] : []) : [day],
    }));
    mock.method(extendedApi, "getCallAnalyticsByCampaign", async () => ({ campaigns: [{ campaign_id: "c0", name: "Running one", series: [day] }] }));
    render(<AppRouterContext.Provider value={router}><Page /></AppRouterContext.Provider>);
    await waitFor(() => assert.equal(screen.queryByTestId("dashboard-kpi-row") !== null, true), { timeout: SLOW });
}

function labelText(testId: string): string | null {
    return screen.queryByTestId(testId)?.textContent ?? null;
}

test("all seven labels say the account's reporting zone, and day labels print the server's date (browser behind UTC)", async () => {
    process.env.TZ = "America/Los_Angeles";
    await mountPage();
    assert.equal(labelText("dashboard-kpi-timezone"), `Timezone: ${REPORTING_ZONE}`);
    assert.equal(labelText("dashboard-stacked-timezone"), `Timezone: ${REPORTING_ZONE}`);
    assert.equal(labelText("dashboard-heatmap-timezone"), `Timezone: ${REPORTING_ZONE}`);
    assert.equal(labelText("dashboard-timeline-timezone"), `Timezone: ${REPORTING_ZONE}`);
    assert.equal(labelText("dashboard-live-timezone"), `Timezone: ${REPORTING_ZONE}`);
    assert.equal(labelText("dashboard-stats-timezone"), `Timezone: ${REPORTING_ZONE}`);
    // The live chart's Custom range keeps the same zone label next to its inputs.
    fireEvent.click(screen.getByRole("button", { name: "Custom" }));
    assert.equal(screen.getByText("Start").closest("label")?.querySelector("input")?.type, "datetime-local");
    assert.equal(labelText("dashboard-live-timezone"), `Timezone: ${REPORTING_ZONE}`);
    // The campaign-lines chart (inside Real-Time Call Statistics) says the same zone.
    assert.equal(labelText("dashboard-campaign-lines-timezone"), null);
    fireEvent.click(screen.getByRole("button", { name: "Campaigns" }));
    assert.equal(labelText("dashboard-campaign-lines-timezone"), `Timezone: ${REPORTING_ZONE}`);
    // No label on the page says UTC.
    assert.equal(screen.queryByText("Timezone: UTC") === null, true);
    // The server's day label "2026-03-01" prints as Mar 1 (local date here would be Feb 28).
    assert.deepEqual(Array.from(screen.getByTestId("stacked-labels").querySelectorAll("li")).map((li) => li.textContent), ["3/1/2026"]);
    assert.deepEqual(Array.from(screen.getByTestId("timeline-labels").querySelectorAll("li")).map((li) => li.textContent), ["3/1/2026"]);
});

test("the day labels print the server's date with the browser ahead of UTC", async () => {
    process.env.TZ = "Asia/Tokyo";
    await mountPage();
    assert.equal(labelText("dashboard-live-timezone"), `Timezone: ${REPORTING_ZONE}`);
    assert.deepEqual(Array.from(screen.getByTestId("stacked-labels").querySelectorAll("li")).map((li) => li.textContent), ["3/1/2026"]);
    assert.deepEqual(Array.from(screen.getByTestId("timeline-labels").querySelectorAll("li")).map((li) => li.textContent), ["3/1/2026"]);
});

test("the campaign lines read and print a bucket's day in the reporting zone, not the UTC day before it", async () => {
    process.env.TZ = "America/Los_Angeles";
    // Today in the reporting zone, so its day bucket is inside the chart's last-4-hours window.
    const today = new Intl.DateTimeFormat("en-CA", { timeZone: REPORTING_ZONE, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
    // No hour rows: the chart draws the day bucket, which starts at Karachi
    // midnight (19:00 UTC of the day before) at any time this test runs.
    await mountPage({ date: today, total_calls: 4, answered: 3, failed: 1 }, false);
    fireEvent.click(screen.getByRole("button", { name: "Campaigns" }));
    const chart = screen.getByTestId("dashboard-campaign-lines-timezone").parentElement?.parentElement?.querySelector("svg");
    assert.equal(chart != null, true, "campaign-lines chart drawn");
    fireEvent.mouseMove(chart as SVGSVGElement, { clientX: 100, clientY: 50 });
    // Karachi midnight is 19:00 UTC of the day before: the bucket's date and
    // its per-campaign count are the reporting zone's day, 4 calls on `today`.
    const expectedDate = new Date(`${today}T12:00:00Z`).toLocaleDateString(undefined, { timeZone: "UTC" });
    await waitFor(() => assert.equal(document.querySelector('[role="tooltip"]') !== null, true, "tooltip shown"));
    const tooltip = document.querySelector('[role="tooltip"]');
    assert.equal(tooltip?.querySelector("div.font-black")?.textContent, expectedDate);
    const rows = Array.from(tooltip?.querySelectorAll("span") ?? []).map((span) => span.textContent);
    assert.deepEqual(rows, ["Running one", "4"]);
});
