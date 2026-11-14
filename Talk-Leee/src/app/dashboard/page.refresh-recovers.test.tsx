/** FX1b Task 9 — when a later refresh succeeds after a failed read, the data replaces the error card without a click. */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
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
const realSetInterval = window.setInterval;
// The page's 30-second refreshes are captured, not scheduled, so the test can
// run one refresh on demand. Every other interval keeps its real timer.
let refreshes: Array<() => unknown> = [];
afterEach(() => { cleanup(); mock.restoreAll(); globalThis.WebSocket = RealWebSocket; window.setInterval = realSetInterval; refreshes = []; });

const day = { date: "2026-03-01", total_calls: 4, answered: 3, failed: 1 };
const campaigns = [{
    id: "c0", name: "Running one", status: "running", direction: "outbound", system_prompt: "", voice_id: "v", max_concurrent_calls: 1,
    total_leads: 10, calls_completed: 3, calls_failed: 1, created_at: "2026-01-01T00:00:00Z",
}] as unknown as Campaign[];
const summary = { timezone: "UTC", total_calls: 0, answered_calls: 0, failed_calls: 0, minutes_used: 0, minutes_remaining: 100, active_campaigns: 1 };
type Source = "summary" | "campaigns" | "daily" | "hourly" | "byCampaign";
const MESSAGE: Record<Source, string> = {
    summary: "Summary is unavailable.", campaigns: "Campaigns are unavailable.", daily: "Daily analytics are unavailable.",
    hourly: "Hourly analytics are unavailable.", byCampaign: "Campaign analytics are unavailable.",
};
const heading = (name: string) => screen.queryByRole("heading", { name }) !== null;
const shown = (text: string) => screen.queryAllByText(text).length;
const retryButtons = () => screen.queryAllByRole("button", { name: "Retry" }).length;

function mountPage(failing: Set<Source>) {
    globalThis.WebSocket = class { constructor() { throw new Error("no socket in tests"); } } as unknown as typeof WebSocket;
    const answer = <T,>(source: Source, value: T) => async () => {
        if (failing.has(source)) throw new Error(MESSAGE[source]);
        return value;
    };
    const calls = {
        summary: mock.method(dashboardApi, "getDashboardSummary", answer("summary", summary)),
        campaigns: mock.method(dashboardApi, "listCampaigns", answer("campaigns", { campaigns })),
        analytics: mock.method(extendedApi, "getCallAnalytics", async (_from: string, _to: string, group: string) =>
            answer(group === "hour" ? "hourly" : "daily", { series: [day] })()),
        byCampaign: mock.method(extendedApi, "getCallAnalyticsByCampaign",
            answer("byCampaign", { campaigns: [{ campaign_id: "c0", name: "Running one", series: [day] }] })),
    };
    window.setInterval = ((handler: TimerHandler, delay?: number, ...rest: unknown[]) => {
        if (delay === 30_000 && typeof handler === "function") {
            refreshes.push(handler as () => unknown);
            return 0;
        }
        return realSetInterval(handler, delay, ...rest);
    }) as typeof window.setInterval;
    render(<AppRouterContext.Provider value={router}><Page /></AppRouterContext.Provider>);
    return calls;
}
const loaded = () => waitFor(() => assert.equal(screen.queryByTestId("dashboard-kpi-row") !== null, true), { timeout: SLOW });

test("a later refresh that succeeds replaces the summary error cards with data, without a click", async () => {
    const failing = new Set<Source>(["summary"]);
    mountPage(failing);
    await loaded();
    assert.equal(shown(MESSAGE.summary) >= 1, true, "the first read failed and its cards show the error");
    for (const name of ["Success Rate", "Minutes Usage", "Real-Time Call Statistics"]) assert.equal(heading(name), false);

    // The server recovers; the 30-second refresh runs. Nothing is clicked.
    failing.clear();
    await waitFor(() => assert.equal(refreshes.length >= 1, true), { timeout: SLOW });
    await act(async () => { await Promise.all(refreshes.map((refresh) => refresh())); });

    await waitFor(() => assert.equal(shown(MESSAGE.summary), 0), { timeout: SLOW });
    assert.equal(retryButtons(), 0);
    for (const name of ["Success Rate", "Minutes Usage", "Real-Time Call Statistics"]) assert.equal(heading(name), true, `${name} shows its data`);
    assert.equal(screen.getByTestId("dashboard-kpi-row").children.length, 4, "the four KPI cards show");
});

test("a refresh that fails again keeps the error card", async () => {
    const failing = new Set<Source>(["summary"]);
    mountPage(failing);
    await loaded();
    await waitFor(() => assert.equal(refreshes.length >= 1, true), { timeout: SLOW });
    await act(async () => { await Promise.all(refreshes.map((refresh) => refresh())); });
    assert.equal(shown(MESSAGE.summary) >= 1, true);
    assert.equal(heading("Success Rate"), false);
});
