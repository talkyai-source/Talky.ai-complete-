/**
 * FX2b Task 1.1, 1.2 — the Dashboard uses the shared states: loading is a
 * status titled "Loading dashboard"; every card with no data shows its own
 * existing empty text (never "Initializing…", never a blank card); an answer
 * the app could not read shows "Please retry.", never the validation dump.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { z } from "zod";
import { dashboardApi } from "@/lib/dashboard-api";
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
        ActivityFeed: () => <div data-testid="activity-feed" />, AlertTimeline: nothing, StackedAreaChart: nothing,
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

const summary = { timezone: "UTC", total_calls: 0, answered_calls: 0, failed_calls: 0, minutes_used: 0, minutes_remaining: 100, active_campaigns: 0 };
const shown = (text: string) => screen.queryAllByText(text).length;
const heading = (name: string) => screen.getByRole("heading", { name });
const card = (name: string) => within(heading(name).closest(".rounded-2xl") as HTMLElement);

function mountEmpty(summaryRead: () => Promise<typeof summary> = async () => summary) {
    globalThis.WebSocket = class { constructor() { throw new Error("no socket in tests"); } } as unknown as typeof WebSocket;
    mock.method(dashboardApi, "getDashboardSummary", summaryRead);
    mock.method(dashboardApi, "listCampaigns", async () => ({ campaigns: [] }));
    mock.method(extendedApi, "getCallAnalytics", async () => ({ series: [] }));
    mock.method(extendedApi, "getCallAnalyticsByCampaign", async () => ({ campaigns: [] }));
    render(<AppRouterContext.Provider value={router}><Page /></AppRouterContext.Provider>);
}
const loaded = () => waitFor(() => assert.equal(screen.queryByTestId("dashboard-kpi-row") !== null, true), { timeout: SLOW });

test("the page loading state is a status titled 'Loading dashboard'", async () => {
    let answer: (() => void) | null = null;
    mountEmpty(() => new Promise((resolve) => { answer = () => resolve(summary); }));
    const status = await screen.findByRole("status");
    assert.equal(within(status).queryByText("Loading dashboard") !== null, true);
    assert.equal(status.querySelector(".animate-spin") === null, true, "the shared LoadingState, not the page's own spinner");
    await waitFor(() => assert.equal(answer !== null, true), { timeout: SLOW });
    await act(async () => { answer!(); });
    await loaded();
    assert.equal(shown("Loading dashboard"), 0);
});

test("the live line chart with no data shows 'No series data', never 'Initializing…'", async () => {
    mountEmpty();
    await loaded();
    assert.equal(shown("Initializing…"), 0);
    assert.equal(card("Call Volume").queryByText("No series data") !== null, true);
});

test("the stacked chart with no data shows 'No series data' in the shared EmptyState", async () => {
    mountEmpty();
    await loaded();
    const text = card("Stacked Area Chart").getByText("No series data");
    assert.equal(text.tagName, "H2", "the shared EmptyState title");
});

test("the Event Stream card with no data shows 'No campaigns yet' instead of a blank card", async () => {
    mountEmpty();
    await loaded();
    assert.equal(card("Event Stream").queryByText("No campaigns yet") !== null, true);
    assert.equal(screen.queryByTestId("activity-feed") === null, true);
});

test("the alert timeline with no data shows 'No alerts' in the shared EmptyState", async () => {
    mountEmpty();
    await loaded();
    assert.equal(card("Error & Alert Timeline").getByText("No alerts").tagName, "H2", "the shared EmptyState title");
});

test("Recent Outbound Campaigns with no data shows 'No campaigns yet' in the shared EmptyState and keeps its link", async () => {
    mountEmpty();
    await loaded();
    const recent = card("Recent Outbound Campaigns");
    assert.equal(recent.getByText("No campaigns yet").tagName, "H2", "the shared EmptyState title");
    assert.equal(recent.getByRole("link", { name: "Create your first campaign" }).getAttribute("href"), "/campaigns/new");
});

test("an answer the app could not read shows 'Please retry.' with Retry, never the validation dump", async () => {
    const dump = z.object({ total_calls: z.number() }).safeParse({ total_calls: "many" });
    assert.equal(dump.success, false);
    mountEmpty(async () => { throw dump.error; });
    await loaded();
    assert.equal(shown("Please retry.") >= 1, true);
    assert.equal(screen.queryAllByRole("button", { name: "Retry" }).length, shown("Please retry."));
    assert.equal(screen.queryByText(/invalid_type|expected number/i) === null, true, "no validation dump");
});
