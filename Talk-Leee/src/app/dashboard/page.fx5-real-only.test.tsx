/**
 * FX5 Task B4 — the Dashboard shows a "vs previous" comparison only for a
 * figure the server sent a previous-period value for, and "Prepare your first
 * campaign" only while the account has no campaign.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { dashboardApi } from "@/lib/dashboard-api";
import { extendedApi } from "@/lib/extended-api";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/calls/call-issues-banner")) return { CallIssuesBanner: nothing };
    if (id.includes("components/dashboard/dialer-insights")) return { DialerInsights: nothing };
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

const summary = { timezone: "UTC", total_calls: 0, answered_calls: 0, failed_calls: 0, minutes_used: 0, minutes_remaining: 100, active_campaigns: 0, active_calls: 2 };
const FIRST_CAMPAIGN = "Prepare your first campaign";
const day = (offset: number) => new Date(Date.now() + offset * 86_400_000).toISOString().slice(0, 10);
/** Day rows around now, so the window before the shown one always holds a server row. */
const DAY_ROWS = [-2, -1, 0, 1].map((offset) => ({ date: day(offset), total_calls: 40, answered: 30, failed: 10 }));

function mount(options: { dayRows?: unknown[]; campaigns?: () => Promise<{ campaigns: unknown[] }> } = {}) {
    globalThis.WebSocket = class { constructor() { throw new Error("no socket in tests"); } } as unknown as typeof WebSocket;
    mock.method(dashboardApi, "getDashboardSummary", async () => summary);
    mock.method(dashboardApi, "listCampaigns", options.campaigns ?? (async () => ({ campaigns: [] })));
    mock.method(extendedApi, "getCallAnalytics", async (_from: string, _to: string, granularity: string) => ({
        series: granularity === "day" ? options.dayRows ?? [] : [],
    }));
    mock.method(extendedApi, "getCallAnalyticsByCampaign", async () => ({ campaigns: [] }));
    render(<AppRouterContext.Provider value={router}><Page /></AppRouterContext.Provider>);
}
const loaded = () => waitFor(() => assert.equal(screen.queryByTestId("dashboard-kpi-row") !== null, true), { timeout: SLOW });
function kpi(title: string) {
    const row = screen.getByTestId("dashboard-kpi-row");
    const card = Array.from(row.children).find((child) => within(child as HTMLElement).queryByText(title) !== null);
    assert.equal(card !== undefined, true, `the ${title} card is shown`);
    return within(card as HTMLElement);
}
const compared = (title: string) => kpi(title).queryByText(/vs previous/) !== null;

test("no previous-period rows from the server: no figure shows 'vs previous'", async () => {
    mount();
    await loaded();
    for (const title of ["Total Calls", "Success Rate", "Active Calls", "Avg Duration"]) assert.equal(compared(title), false, title);
    assert.equal(screen.queryAllByText(/vs previous/).length, 0);
});

test("previous-period rows from the server: only the figures they carry are compared", async () => {
    mount({ dayRows: DAY_ROWS });
    await loaded();
    await waitFor(() => assert.equal(compared("Total Calls"), true), { timeout: SLOW });
    assert.equal(compared("Success Rate"), true, "answered / total_calls of the previous rows");
    assert.equal(compared("Active Calls"), false, "the summary has no previous active_calls");
    assert.equal(compared("Avg Duration"), false, "the rows carry no duration");
});

test("'Prepare your first campaign' shows for an account with no campaign", async () => {
    mount();
    await loaded();
    await waitFor(() => assert.equal(screen.queryByRole("heading", { name: FIRST_CAMPAIGN }) !== null, true), { timeout: SLOW });
});

test("'Prepare your first campaign' is not shown once the account has a campaign", async () => {
    const read = mock.fn(async () => ({ campaigns: [{ id: "camp-1", name: "Spring push", status: "draft", direction: "inbound" }] }));
    mount({ campaigns: read });
    await loaded();
    await waitFor(() => assert.equal(read.mock.callCount() > 0, true), { timeout: SLOW });
    assert.equal(screen.queryByRole("heading", { name: FIRST_CAMPAIGN }) === null, true);
});

test("'Prepare your first campaign' is not shown while the campaign list is unknown", async () => {
    mount({ campaigns: async () => { throw new Error("Campaign service is unavailable."); } });
    await loaded();
    assert.equal(screen.queryByRole("heading", { name: FIRST_CAMPAIGN }) === null, true);
});
