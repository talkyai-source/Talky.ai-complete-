/**
 * FX3a3 Task 2.1 — the campaign feed on the Dashboard prints a campaign's
 * date in the app's most used date form. Same boundary stubs as
 * page.timezone.test.tsx, except that the feed stub prints each item's time.
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
    if (id.includes("components/dashboard/dialer-insights")) return { DialerInsights: nothing };
    if (id.includes("components/dashboard/first-call-setup")) return { FirstCallSetup: nothing };
    if (id.includes("components/ui/dashboard-charts")) return {
        ActivityFeed: ({ items }: { items: Array<{ id: string; timeLabel: string }> }) => (
            <ul data-testid="feed-times">{items.map((item) => <li key={item.id}>{item.timeLabel}</li>)}</ul>
        ),
        AlertTimeline: nothing, StackedAreaChart: nothing, DonutChart: nothing, Heatmap: nothing, LiveCallsTimeSeriesChart: nothing,
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
const CREATED_AT = "2026-01-15T12:00:00Z";

afterEach(() => {
    cleanup();
    mock.restoreAll();
    globalThis.WebSocket = RealWebSocket;
});

test("FX3a3: the campaign feed shows the campaign's date in the default date form", async () => {
    globalThis.WebSocket = class { constructor() { throw new Error("no socket in tests"); } } as unknown as typeof WebSocket;
    const campaigns = [{
        id: "c0", name: "Running one", status: "running", direction: "outbound", system_prompt: "", voice_id: "v", max_concurrent_calls: 1,
        total_leads: 10, calls_completed: 3, calls_failed: 1, created_at: CREATED_AT,
    }] as unknown as Campaign[];
    mock.method(dashboardApi, "getDashboardSummary", async () => ({
        total_calls: 0, answered_calls: 0, failed_calls: 0, minutes_used: 0, minutes_remaining: 100, active_campaigns: 1,
    }));
    mock.method(dashboardApi, "listCampaigns", async () => ({ campaigns }));
    mock.method(extendedApi, "getCallAnalytics", async () => ({ series: [] }));
    mock.method(extendedApi, "getCallAnalyticsByCampaign", async () => ({ campaigns: [] }));
    render(<AppRouterContext.Provider value={router}><Page /></AppRouterContext.Provider>);
    await waitFor(() => assert.equal(screen.queryByTestId("feed-times") !== null, true), { timeout: SLOW });
    const times = Array.from(screen.getByTestId("feed-times").querySelectorAll("li")).map((li) => li.textContent);
    assert.equal(times.includes(new Date(CREATED_AT).toLocaleDateString()), true, times.join(" | "));
    assert.equal(times.includes("Jan 15"), false);
});
