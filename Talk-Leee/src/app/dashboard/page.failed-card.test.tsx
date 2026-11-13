/**
 * FX1a Task 5.1 — the Dashboard never blanks on one failure: a failed first
 * request replaces only the cards that read it with the shared ErrorState
 * (server message + Retry); every other card still shows.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
const summary = { total_calls: 0, answered_calls: 0, failed_calls: 0, minutes_used: 0, minutes_remaining: 100, active_campaigns: 1 };
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
    render(<AppRouterContext.Provider value={router}><Page /></AppRouterContext.Provider>);
    return calls;
}
const loaded = () => waitFor(() => assert.equal(screen.queryByTestId("dashboard-kpi-row") !== null, true), { timeout: SLOW });

test("a failed hourly request replaces only the Heatmap with the shared ErrorState; every other card still shows", async () => {
    const failing = new Set<Source>(["hourly"]);
    const calls = mountPage(failing);
    await loaded();
    assert.equal(shown(MESSAGE.hourly), 1, "one card shows the server's message");
    assert.equal(retryButtons(), 1);
    assert.equal(heading("Heatmap"), false, "the failed card is replaced");
    for (const name of ["Call Volume", "Success Rate", "Minutes Usage", "Stacked Area Chart", "Real-Time Call Statistics",
        "Event Stream", "Error & Alert Timeline", "Recent Outbound Campaigns"]) {
        assert.equal(heading(name), true, `${name} still shows`);
    }
    assert.equal(screen.getByTestId("dashboard-kpi-row").children.length, 4, "the four KPI cards still show");
    assert.equal(screen.queryByTestId("dialer-insights") !== null, true);
    assert.equal(screen.queryAllByText("Running one").length > 0, true, "data from the other requests is on screen");

    // Retry re-reads only the failed source and puts the card back.
    const before = { summary: calls.summary.mock.callCount(), campaigns: calls.campaigns.mock.callCount(), analytics: calls.analytics.mock.callCount() };
    failing.clear();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => assert.equal(heading("Heatmap"), true), { timeout: SLOW });
    assert.equal(shown(MESSAGE.hourly), 0);
    assert.equal(calls.analytics.mock.callCount(), before.analytics + 1);
    assert.equal(calls.summary.mock.callCount(), before.summary);
    assert.equal(calls.campaigns.mock.callCount(), before.campaigns);
});

const CASES: Array<{ source: Source; gone: string[]; kept: string[] }> = [
    { source: "summary", gone: ["Success Rate", "Minutes Usage", "Real-Time Call Statistics"], kept: ["Heatmap", "Stacked Area Chart", "Recent Outbound Campaigns", "Event Stream"] },
    { source: "campaigns", gone: ["Event Stream", "Recent Outbound Campaigns"], kept: ["Success Rate", "Heatmap", "Stacked Area Chart", "Real-Time Call Statistics"] },
    { source: "daily", gone: ["Stacked Area Chart", "Error & Alert Timeline"], kept: ["Success Rate", "Heatmap", "Recent Outbound Campaigns", "Real-Time Call Statistics"] },
    { source: "byCampaign", gone: ["Real-Time Call Statistics"], kept: ["Success Rate", "Heatmap", "Stacked Area Chart", "Recent Outbound Campaigns"] },
];
for (const { source, gone, kept } of CASES) {
    test(`a failed ${source} request never blanks the page: only its cards show the error`, async () => {
        mountPage(new Set<Source>([source]));
        await loaded();
        assert.equal(shown(MESSAGE[source]) >= 1, true, "the server's message is shown");
        assert.equal(retryButtons(), shown(MESSAGE[source]));
        for (const name of gone) assert.equal(heading(name), false, `${name} is replaced by the error`);
        for (const name of kept) assert.equal(heading(name), true, `${name} still shows`);
        assert.equal(heading("Call Volume"), true);
    });
}
