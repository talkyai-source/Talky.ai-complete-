import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { dashboardApi, type Campaign } from "@/lib/dashboard-api";
import { extendedApi } from "@/lib/extended-api";

// Plan 3.7 Z3 on the Dashboard: every date-sensitive figure says which zone
// it really uses, and the UTC figures print the UTC date. The real page with
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

const day = { date: "2026-03-01", total_calls: 4, answered: 3, failed: 1 };

async function mountPage() {
    globalThis.WebSocket = class { constructor() { throw new Error("no socket in tests"); } } as unknown as typeof WebSocket;
    const campaigns = [{
        id: "c0", name: "Running one", status: "running", direction: "outbound", system_prompt: "", voice_id: "v", max_concurrent_calls: 1,
        total_leads: 10, calls_completed: 3, calls_failed: 1, created_at: "2026-01-01T00:00:00Z",
    }] as unknown as Campaign[];
    mock.method(dashboardApi, "getDashboardSummary", async () => ({
        total_calls: 0, answered_calls: 0, failed_calls: 0, minutes_used: 0, minutes_remaining: 100, active_campaigns: 1,
    }));
    mock.method(dashboardApi, "listCampaigns", async () => ({ campaigns }));
    mock.method(extendedApi, "getCallAnalytics", async () => ({ series: [day] }));
    mock.method(extendedApi, "getCallAnalyticsByCampaign", async () => ({ campaigns: [{ campaign_id: "c0", name: "Running one", series: [day] }] }));
    render(<AppRouterContext.Provider value={router}><Page /></AppRouterContext.Provider>);
    await waitFor(() => assert.equal(screen.queryByTestId("dashboard-kpi-row") !== null, true), { timeout: SLOW });
}

function labelText(testId: string): string | null {
    return screen.queryByTestId(testId)?.textContent ?? null;
}

test("the month figures say UTC, the live chart says the browser's zone, and UTC figures print the UTC date (zone behind UTC)", async () => {
    process.env.TZ = "America/Los_Angeles";
    await mountPage();
    assert.equal(labelText("dashboard-kpi-timezone"), "Timezone: UTC");
    assert.equal(labelText("dashboard-stacked-timezone"), "Timezone: UTC");
    assert.equal(labelText("dashboard-heatmap-timezone"), "Timezone: UTC");
    assert.equal(labelText("dashboard-timeline-timezone"), "Timezone: UTC");
    assert.equal(labelText("dashboard-live-timezone"), "Timezone: America/Los_Angeles");
    assert.equal(labelText("dashboard-stats-timezone"), "Timezone: America/Los_Angeles");
    // The live chart's Custom range keeps the same zone label next to its inputs.
    fireEvent.click(screen.getByRole("button", { name: "Custom" }));
    assert.equal(screen.getByText("Start").closest("label")?.querySelector("input")?.type, "datetime-local");
    assert.equal(labelText("dashboard-live-timezone"), "Timezone: America/Los_Angeles");
    // The campaign-lines chart (inside Real-Time Call Statistics) says UTC.
    assert.equal(labelText("dashboard-campaign-lines-timezone"), null);
    fireEvent.click(screen.getByRole("button", { name: "Campaigns" }));
    assert.equal(labelText("dashboard-campaign-lines-timezone"), "Timezone: UTC");
    // The UTC day bucket "2026-03-01" prints as Mar 1 (local date here would be Feb 28).
    assert.deepEqual(Array.from(screen.getByTestId("stacked-labels").querySelectorAll("li")).map((li) => li.textContent), ["Mar 1"]);
    assert.deepEqual(Array.from(screen.getByTestId("timeline-labels").querySelectorAll("li")).map((li) => li.textContent), ["Mar 1"]);
});

test("the UTC figures print the UTC date with the browser ahead of UTC", async () => {
    process.env.TZ = "Asia/Tokyo";
    await mountPage();
    assert.equal(labelText("dashboard-live-timezone"), "Timezone: Asia/Tokyo");
    assert.deepEqual(Array.from(screen.getByTestId("stacked-labels").querySelectorAll("li")).map((li) => li.textContent), ["Mar 1"]);
    assert.deepEqual(Array.from(screen.getByTestId("timeline-labels").querySelectorAll("li")).map((li) => li.textContent), ["Mar 1"]);
});
