import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { dashboardApi, type Campaign } from "@/lib/dashboard-api";
import { extendedApi } from "@/lib/extended-api";

// Plan 3.3 (SS4) on the dashboard: the real page, with the layout, the charts
// and the panels that fetch on their own stubbed. The activity feed stub prints
// each item's detail line, which is where the page writes the campaign status.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/calls/call-issues-banner")) return { CallIssuesBanner: nothing };
    if (id.includes("components/dashboard/dialer-insights")) return { DialerInsights: nothing };
    if (id.includes("components/dashboard/first-call-setup")) return { FirstCallSetup: nothing };
    if (id.includes("components/ui/dashboard-charts")) return {
        ActivityFeed: ({ items }: { items: Array<{ id: string; detail: string }> }) => (
            <ul data-testid="activity-feed">{items.map((item) => <li key={item.id}>{item.detail}</li>)}</ul>
        ),
        AlertTimeline: nothing, DonutChart: nothing, Heatmap: nothing, LiveCallsTimeSeriesChart: nothing, StackedAreaChart: nothing,
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

// Generous: the full suite runs many files in parallel on a loaded machine.
const SLOW = 15_000;
const RealWebSocket = globalThis.WebSocket;
const router = { push() {}, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } as unknown as React.ContextType<typeof AppRouterContext>;

afterEach(() => {
    cleanup();
    mock.restoreAll();
    globalThis.WebSocket = RealWebSocket;
});

const labelled: Array<[string, string, string]> = [
    ["Draft one", "draft", "Draft"],
    ["Running one", "running", "Running"],
    ["Paused one", "paused", "Paused"],
    ["Stopped one", "stopped", "Stopped"],
    ["Scheduled one", "scheduled", "Scheduled"],
];

test("the dashboard shows each campaign's status with the shared label", async () => {
    // The live stream never connects in a unit test: construction fails, the page falls back to its REST data.
    globalThis.WebSocket = class { constructor() { throw new Error("no socket in tests"); } } as unknown as typeof WebSocket;
    const campaigns = labelled.map(([name, status], index) => ({
        id: `c${index}`, name, status, direction: "outbound", system_prompt: "", voice_id: "v", max_concurrent_calls: 1,
        total_leads: 10, calls_completed: 3, calls_failed: 1, created_at: "2026-01-01T00:00:00Z",
    })) as unknown as Campaign[];
    mock.method(dashboardApi, "getDashboardSummary", async () => ({
        total_calls: 0, answered_calls: 0, failed_calls: 0, minutes_used: 0, minutes_remaining: 100, active_campaigns: 1,
    }));
    mock.method(dashboardApi, "listCampaigns", async () => ({ campaigns }));
    mock.method(extendedApi, "getCallAnalytics", async () => ({ series: [] }));
    mock.method(extendedApi, "getCallAnalyticsByCampaign", async () => ({ campaigns: [] }));

    render(<AppRouterContext.Provider value={router}><Page /></AppRouterContext.Provider>);
    await waitFor(() => assert.equal(screen.queryByTestId("activity-feed") !== null, true), { timeout: SLOW });

    // The activity feed's detail line.
    const details = Array.from(screen.getByTestId("activity-feed").querySelectorAll("li")).map((li) => li.textContent ?? "");
    assert.deepEqual(
        details.slice(0, labelled.length),
        labelled.map(([, , label]) => `3 completed • 1 failed • ${label}`),
    );

    // The "recent campaigns" list: the badge next to each campaign's name.
    for (const [name, status, label] of labelled) {
        const link = screen.getByRole("heading", { name }).closest("a")!;
        const badge = link.querySelector("span")!.textContent;
        assert.equal(badge, label, `${name}: badge`);
        assert.equal(badge === status, false, `${name}: never the raw value`);
    }
});
