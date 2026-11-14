/**
 * FX3b Task 1 — Dashboard texts. The call-volume and success-rate cards are
 * named for their content (the names Analytics already uses), and the outcome
 * view prints the same outcome names as Call History (lib/call-outcome-label).
 * Same boundary stubs as page.feed-date.test.tsx.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { dashboardApi } from "@/lib/dashboard-api";
import { extendedApi } from "@/lib/extended-api";
import { humanizeOutcome } from "@/lib/call-outcome-label";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/calls/call-issues-banner")) return { CallIssuesBanner: nothing };
    if (id.includes("components/dashboard/dialer-insights")) return { DialerInsights: nothing };
    if (id.includes("components/dashboard/first-call-setup")) return { FirstCallSetup: nothing };
    if (id.includes("components/ui/dashboard-charts")) return {
        ActivityFeed: nothing, AlertTimeline: nothing, StackedAreaChart: nothing, DonutChart: nothing, Heatmap: nothing, LiveCallsTimeSeriesChart: nothing,
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
const realRequestAnimationFrame = window.requestAnimationFrame;
const realCancelAnimationFrame = window.cancelAnimationFrame;
const router = { push() {}, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } as unknown as React.ContextType<typeof AppRouterContext>;
const headings = () => Array.from(document.querySelectorAll("h3")).map((node) => node.textContent);
const shown = (text: string) => screen.queryAllByText(text).length > 0;

afterEach(() => {
    cleanup();
    mock.restoreAll();
    globalThis.WebSocket = RealWebSocket;
    window.requestAnimationFrame = realRequestAnimationFrame;
    window.cancelAnimationFrame = realCancelAnimationFrame;
});

async function mount() {
    globalThis.WebSocket = class { constructor() { throw new Error("no socket in tests"); } } as unknown as typeof WebSocket;
    mock.method(dashboardApi, "getDashboardSummary", async () => ({
        timezone: "UTC", total_calls: 4, answered_calls: 0, failed_calls: 4, minutes_used: 0, minutes_remaining: 100, active_campaigns: 0,
        outcome_breakdown: { no_answer: 2, timeout: 1, goal_not_achieved: 1 },
    }));
    mock.method(dashboardApi, "listCampaigns", async () => ({ campaigns: [] }));
    mock.method(extendedApi, "getCallAnalytics", async () => ({ series: [] }));
    mock.method(extendedApi, "getCallAnalyticsByCampaign", async () => ({ campaigns: [] }));
    render(<AppRouterContext.Provider value={router}><Page /></AppRouterContext.Provider>);
    await waitFor(() => assert.equal(headings().includes("Minutes Usage"), true), { timeout: SLOW });
}

test("FX3b 1.1: the call-volume and success-rate cards are named for their content", async () => {
    await mount();
    assert.equal(headings().includes("Call Volume"), true, headings().join(" | "));
    assert.equal(headings().includes("Live Line Chart"), false);
    assert.equal(headings().includes("Success Rate"), true, headings().join(" | "));
    assert.equal(headings().includes("Donut Chart"), false);
});

test("FX3b 1.3: the outcome view prints the Call History outcome names", async () => {
    await mount();
    // The hover tooltip is placed on the next frame; jsdom here has no frames.
    window.requestAnimationFrame = ((callback: FrameRequestCallback) => window.setTimeout(() => callback(0), 0)) as typeof window.requestAnimationFrame;
    window.cancelAnimationFrame = ((id: number) => window.clearTimeout(id)) as typeof window.cancelAnimationFrame;
    fireEvent.click(screen.getByRole("button", { name: "Outcomes" }));
    // The chart names a segment in its hover tooltip.
    await waitFor(() => assert.equal(document.querySelectorAll('svg[viewBox="0 0 220 220"] path').length, 3), { timeout: SLOW });
    const seen = new Set<string>();
    for (const segment of Array.from(document.querySelectorAll('svg[viewBox="0 0 220 220"] path'))) {
        fireEvent.mouseEnter(segment, { clientX: 10, clientY: 10 });
        await act(async () => { await new Promise((resolve) => setTimeout(resolve, 30)); });
        for (const key of ["no_answer", "timeout", "goal_not_achieved"]) if (shown(humanizeOutcome(key))) seen.add(humanizeOutcome(key));
        for (const old of ["No answer", "Timeout", "Not qualified"]) if (shown(old)) seen.add(old);
        fireEvent.mouseLeave(segment);
    }
    assert.deepEqual(Array.from(seen).sort(), ["Disqualified", "No Answer", "Timed Out"]);
});
