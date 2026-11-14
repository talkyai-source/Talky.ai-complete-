import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const zone = "Asia/Karachi";
const savedSummary = { timezone: zone, total_calls: 5, answered_calls: 3, failed_calls: 2,
    minutes_used: 37, minutes_remaining: 53, minutes_included: 90, minutes_state: "known",
    active_campaigns: 1, active_calls: 0, avg_call_duration_seconds: 60, queued_jobs: 0 };
const originalSeries = [{ date: "2026-11-06", total_calls: 5, answered: 3, failed: 2, goal_achieved: 1 }];
const nextSeries = [{ ...originalSeries[0], total_calls: 9, answered: 7 }];
type Analytics = { timezone: string; series: typeof originalSeries };
type Call = { method: string; args: unknown[] };
let calls: Call[];
let summary: () => Promise<typeof savedSummary>;
let daily: () => Promise<Analytics>;
let hourlySeries: { date: string; total_calls: number; answered: number; failed: number; goal_achieved: number }[];
const dashboard = {
    getDashboardSummary: async (...args: unknown[]) => { calls.push({ method: "summary", args }); return summary(); },
    listCampaigns: async () => ({ campaigns: [] }),
};
const extended = {
    getCallAnalytics: async (...args: unknown[]) => {
        calls.push({ method: args[2] === "day" ? "daily" : "hourly", args });
        return args[2] === "day" ? daily() : { timezone: zone, series: hourlySeries };
    },
    getCallAnalyticsByCampaign: async (...args: unknown[]) => {
        calls.push({ method: "campaign", args }); return { timezone: zone, campaigns: [] };
    },
};
type LiveProps = { timeZone: string; buckets: { total: number; answered: number; failed: number; startMs: number; endMs: number }[] };
type StackedProps = { points: { label: string; a: number; b: number }[] };
const Nothing = () => null;
const motionTags = new Map<string, React.ComponentType<Record<string, unknown>>>();
const motion = new Proxy({}, { get: (_target, tag: string) => {
    if (!motionTags.has(tag)) motionTags.set(tag, function Motion({ children, ...props }) {
        const allowed = Object.fromEntries(Object.entries(props).filter(([key]) =>
            !["initial", "animate", "exit", "transition", "whileHover", "whileTap", "layout"].includes(key)));
        return React.createElement(tag, allowed, children as React.ReactNode);
    });
    return motionTags.get(tag);
} });
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
// Exercise the real page's fetches, state and transforms. Child chart ports
// expose the data they receive instead of reimplementing the page's consumers.
loader._load = function (id, ...args) {
    if (id.includes("lib/extended-api")) return { extendedApi: extended };
    if (id.includes("lib/dashboard-api")) return { dashboardApi: dashboard };
    if (id.includes("components/layout/dashboard-layout")) return {
        DashboardLayout: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
    };
    if (id === "framer-motion") return { motion, animate: () => ({ stop() {} }), useInView: () => false, useReducedMotion: () => true };
    if (id.includes("components/ui/dashboard-charts")) return {
        ActivityFeed: Nothing, AlertTimeline: Nothing, DonutChart: Nothing, Heatmap: Nothing,
        LiveCallsTimeSeriesChart: (props: LiveProps) => <output data-testid="live-chart">{JSON.stringify(props)}</output>,
        StackedAreaChart: (props: StackedProps) => <output data-testid="stacked-chart">{JSON.stringify(props)}</output>,
    };
    if (id.includes("components/calls/call-issues-banner")) return { CallIssuesBanner: Nothing };
    if (id.includes("components/dashboard/first-call-setup")) return { FirstCallSetup: Nothing };
    if (id.includes("components/dashboard/dialer-insights")) return { DialerInsights: Nothing };
    return originalLoad.call(this, id, ...args);
};
let DashboardPage: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- scoped transport/visual ports; actual page is mounted
    DashboardPage = require("./page").default;
} finally { loader._load = originalLoad; }

type Timer = { delay: number; callback: () => void };
let intervals: Map<number, Timer>;
let timerId: number;
let externalRequests: unknown[];
let sockets: string[];
let socketDescriptor: PropertyDescriptor | undefined;
const originalUrl = process.env.NEXT_PUBLIC_DASHBOARD_WS_URL;
function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>(done => { resolve = done; });
    return { promise, resolve };
}
function count(method: string) { return calls.filter(call => call.method === method).length; }
async function charts(answered: number, failed: number) {
    await waitFor(() => {
        const live = JSON.parse(screen.getByTestId("live-chart").textContent!) as LiveProps;
        const stacked = JSON.parse(screen.getByTestId("stacked-chart").textContent!) as StackedProps;
        assert.equal(live.timeZone, zone);
        assert.equal(live.buckets.length, 1);
        assert.equal(live.buckets[0].answered, answered);
        assert.equal(live.buckets[0].failed, failed);
        assert.equal(live.buckets[0].total, answered + failed);
        assert.equal(new Date(live.buckets[0].startMs).toISOString(), "2026-11-05T19:00:00.000Z");
        assert.equal(new Date(live.buckets[0].endMs).toISOString(), "2026-11-06T19:00:00.000Z");
        assert.equal(stacked.points.length, 1);
        assert.equal(stacked.points[0].a, answered);
        assert.equal(stacked.points[0].b, failed);
        assert.match(stacked.points[0].label, /11\/6\/2026/);
    });
}
async function tick() {
    await act(async () => {
        for (const timer of [...intervals.values()]) if (timer.delay === 30_000) timer.callback();
    });
}
beforeEach(() => {
    calls = []; externalRequests = []; sockets = []; intervals = new Map(); timerId = 0;
    summary = async () => savedSummary;
    daily = async () => ({ timezone: zone, series: originalSeries });
    hourlySeries = [];
    delete process.env.NEXT_PUBLIC_DASHBOARD_WS_URL;
    mock.timers.enable({ apis: ["Date"], now: new Date("2026-11-06T10:00:00Z") });
    mock.method(globalThis, "fetch", async (...args: unknown[]) => {
        externalRequests.push(args); throw new Error("Unexpected dashboard transport");
    });
    socketDescriptor = Object.getOwnPropertyDescriptor(globalThis, "WebSocket");
    Object.defineProperty(globalThis, "WebSocket", { configurable: true, writable: true, value: class {
        constructor(url: string) { sockets.push(url); throw new Error("No configured stream in analytics test"); }
    } });
    mock.method(window, "setInterval", (handler: TimerHandler, delay?: number) => {
        assert.equal(typeof handler, "function");
        const id = ++timerId; intervals.set(id, { delay: delay ?? 0, callback: handler as () => void }); return id;
    });
    mock.method(window, "clearInterval", (id?: number) => { if (id !== undefined) intervals.delete(id); });
});
afterEach(() => {
    cleanup(); mock.restoreAll(); mock.timers.reset();
    if (socketDescriptor) Object.defineProperty(globalThis, "WebSocket", socketDescriptor);
    else Reflect.deleteProperty(globalThis, "WebSocket");
    if (originalUrl === undefined) delete process.env.NEXT_PUBLIC_DASHBOARD_WS_URL;
    else process.env.NEXT_PUBLIC_DASHBOARD_WS_URL = originalUrl;
    assert.deepEqual(externalRequests, []);
    assert.deepEqual(sockets, []);
});

test("a delayed summary keeps the existing loading state and loads one daily slice for both charts", async () => {
    const pending = deferred<typeof savedSummary>();
    summary = () => pending.promise;
    render(<DashboardPage />);
    assert.match(screen.getByRole("status").textContent ?? "", /Loading dashboard/);
    assert.equal(count("summary"), 1);
    assert.equal(count("daily") + count("hourly") + count("campaign"), 0);
    await act(async () => { pending.resolve(savedSummary); });
    await charts(3, 2);
    assert.equal(screen.queryByText("Loading dashboard"), null);
    assert.equal(count("daily"), 1, "The identical day/month/outbound slice has one request owner");
    assert.equal(count("hourly"), 1);
    assert.equal(count("campaign"), 1);
    for (const call of calls.filter(call => ["daily", "hourly", "campaign"].includes(call.method))) {
        assert.deepEqual(call.args, ["2026-11-01", "2026-11-06", call.method === "hourly" ? "hour" : "day", "outbound", false, zone]);
    }
});

test("the same pending daily response feeds both consumers without hiding the loading state", async () => {
    const pending = deferred<Analytics>();
    daily = () => pending.promise;
    render(<DashboardPage />);
    await waitFor(() => assert.equal(count("daily"), 1));
    assert.match(screen.getByRole("status").textContent ?? "", /Loading dashboard/);
    assert.equal(screen.queryByTestId("live-chart"), null);
    assert.equal(screen.queryByTestId("stacked-chart"), null);
    await act(async () => { pending.resolve({ timezone: zone, series: originalSeries }); });
    await charts(3, 2);
    assert.equal(count("daily"), 1);
});

test("one periodic daily refresh updates both consumers and unmount releases polling", async () => {
    const view = render(<DashboardPage />);
    await charts(3, 2);
    const before = count("daily");
    daily = async () => ({ timezone: zone, series: nextSeries });
    await tick();
    await charts(7, 2);
    assert.equal(count("daily"), before + 1);
    // QA F1-30: each poll also refreshes the live chart's recent hours (three
    // calendar days), never the month of hourly data behind the heatmap.
    const hourly = calls.filter((call) => call.method === "hourly");
    assert.equal(hourly.length, 2);
    assert.deepEqual(hourly[1].args, ["2026-11-04", "2026-11-06", "hour", "outbound", false, zone]);
    assert.equal(count("campaign"), 1);
    assert.equal(count("summary"), 2, "The separate REST summary fallback remains active");
    view.unmount();
    assert.equal(intervals.size, 0);
});

test("a failed initial daily request stays visible and Retry loads one new shared slice", async () => {
    daily = async () => { throw new Error("Synthetic daily analytics failure"); };
    render(<DashboardPage />);
    // FX1a: a failed source replaces only the cards that read it, each with
    // its own message and Retry; Retry reads that source again and no other.
    const alerts = await screen.findAllByRole("alert");
    for (const alert of alerts) assert.match(alert.textContent ?? "", /Synthetic daily analytics failure/);
    assert.equal(count("daily"), 1);
    daily = async () => ({ timezone: zone, series: originalSeries });
    fireEvent.click(screen.getAllByRole("button", { name: "Retry" })[0]);
    await charts(3, 2);
    assert.equal(count("daily"), 2, "Exactly one failed request and one successful retry");
    assert.equal(count("hourly"), 1);
    assert.equal(count("campaign"), 1);
    assert.equal(screen.queryByRole("alert"), null);
});

test("a transient daily poll failure preserves both snapshots and the next poll recovers", async () => {
    render(<DashboardPage />);
    await charts(3, 2);
    const before = count("daily");
    daily = async () => { throw new Error("Synthetic transient analytics failure"); };
    await tick();
    await charts(3, 2);
    assert.equal(count("daily"), before + 1);
    daily = async () => ({ timezone: zone, series: nextSeries });
    await tick();
    await charts(7, 2);
    assert.equal(count("daily"), before + 2);
});

// QA F1-30 (QA-UI-139): the live chart was built from day rows, so the 1h, 4h
// and 24h ranges all showed the same single day.
test("the 1h, 4h and 24h ranges show the hours they cover", async () => {
    mock.timers.setTime(Date.parse("2026-11-06T10:30:00Z")); // 15:30 in Karachi
    hourlySeries = [
        { date: "2026-11-06T08:00", total_calls: 4, answered: 4, failed: 0, goal_achieved: 0 }, // 03:00-04:00Z
        { date: "2026-11-06T13:00", total_calls: 2, answered: 1, failed: 1, goal_achieved: 0 }, // 08:00-09:00Z
        { date: "2026-11-06T15:00", total_calls: 1, answered: 1, failed: 0, goal_achieved: 0 }, // 10:00-11:00Z
    ];
    render(<DashboardPage />);
    const liveTotal = () => {
        const live = JSON.parse(screen.getByTestId("live-chart").textContent!) as LiveProps;
        return live.buckets.reduce((sum, bucket) => sum + bucket.total, 0);
    };
    await waitFor(() => assert.equal(liveTotal(), 3, "4h (default) covers 06:30-10:30Z"));
    fireEvent.click(screen.getByRole("button", { name: "1h" }));
    await waitFor(() => assert.equal(liveTotal(), 1, "1h covers 09:30-10:30Z"));
    fireEvent.click(screen.getByRole("button", { name: "24h" }));
    await waitFor(() => assert.equal(liveTotal(), 7, "24h covers every hour above"));
});

// QA F1-34 (QA-UI-092): on a slow connection the dashboard showed a lone
// spinner for 10 s. It now shows the shape of the cards and chart it loads.
test("while the dashboard loads, its cards and chart are shown as placeholders", async () => {
    const pending = deferred<typeof savedSummary>();
    summary = () => pending.promise;
    render(<DashboardPage />);
    const status = screen.getByRole("status");
    assert.match(status.textContent ?? "", /Loading dashboard/);
    assert.ok(status.querySelectorAll("[aria-busy='true']").length >= 5, "four card placeholders and a chart placeholder");
    assert.equal(status.querySelector(".animate-spin"), null, "no lone spinner");
    await act(async () => { pending.resolve(savedSummary); });
    await waitFor(() => assert.equal(screen.queryByText("Loading dashboard"), null));
});
