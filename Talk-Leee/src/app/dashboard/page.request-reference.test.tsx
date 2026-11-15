/**
 * FX4a Task 1 — a Dashboard card whose request failed shows that request's
 * reference in its ErrorState ("Request reference: …"); a failure without one
 * shows no reference line.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { dashboardApi, type Campaign } from "@/lib/dashboard-api";
import { extendedApi } from "@/lib/extended-api";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/calls/call-issues-banner")) return { CallIssuesBanner: nothing };
    if (id.includes("components/dashboard/dialer-insights")) return { DialerInsights: () => <div data-testid="dialer-insights" /> };
    if (id.includes("components/dashboard/first-call-setup")) return { FirstCallSetup: nothing };
    if (id.includes("components/ui/dashboard-charts")) return {
        ActivityFeed: nothing, AlertTimeline: nothing, StackedAreaChart: nothing,
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
const router = { push() {}, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } as unknown as React.ContextType<typeof AppRouterContext>;
afterEach(() => { cleanup(); mock.restoreAll(); globalThis.WebSocket = RealWebSocket; });

const day = { date: "2026-03-01", total_calls: 4, answered: 3, failed: 1 };
const campaigns = [{
    id: "c0", name: "Running one", status: "running", direction: "outbound", system_prompt: "", voice_id: "v", max_concurrent_calls: 1,
    total_leads: 10, calls_completed: 3, calls_failed: 1, created_at: "2026-01-01T00:00:00Z",
}] as unknown as Campaign[];
const summary = { timezone: "UTC", total_calls: 0, answered_calls: 0, failed_calls: 0, minutes_used: 0, minutes_remaining: 100, active_campaigns: 1 };

function mountPage(hourlyFailure: Error) {
    globalThis.WebSocket = class { constructor() { throw new Error("no socket in tests"); } } as unknown as typeof WebSocket;
    mock.method(dashboardApi, "getDashboardSummary", async () => summary);
    mock.method(dashboardApi, "listCampaigns", async () => ({ campaigns }));
    mock.method(extendedApi, "getCallAnalytics", async (_from: string, _to: string, group: string) => {
        if (group === "hour") throw hourlyFailure;
        return { series: [day] };
    });
    mock.method(extendedApi, "getCallAnalyticsByCampaign", async () => ({ campaigns: [{ campaign_id: "c0", name: "Running one", series: [day] }] }));
    render(<AppRouterContext.Provider value={router}><Page /></AppRouterContext.Provider>);
}
const loaded = () => waitFor(() => assert.equal(screen.queryByTestId("dashboard-kpi-row") !== null, true), { timeout: SLOW });

test("the failed card shows the failed request's reference", async () => {
    mountPage(Object.assign(new Error("Hourly analytics are unavailable."), { requestId: "req-dash-4410" }));
    await loaded();
    await waitFor(() => assert.equal(screen.queryAllByText("Hourly analytics are unavailable.").length, 1), { timeout: SLOW });
    assert.equal(screen.queryAllByText("Request reference: req-dash-4410").length, 1);
});

test("a failure without a reference shows no reference line", async () => {
    mountPage(new Error("Hourly analytics are unavailable."));
    await loaded();
    await waitFor(() => assert.equal(screen.queryAllByText("Hourly analytics are unavailable.").length, 1), { timeout: SLOW });
    assert.equal(screen.queryAllByText(/Request reference/).length, 0);
});
