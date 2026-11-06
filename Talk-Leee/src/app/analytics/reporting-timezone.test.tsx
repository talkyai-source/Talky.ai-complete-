import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

type Call = { method: string; args: unknown[] };
let calls: Call[] = [];
let chartZones: unknown[] = [];
let summary: () => Promise<unknown>;
let socketDescriptor: PropertyDescriptor | undefined;
const zone = "Asia/Karachi";
const series = [{ date: "2026-11-01", total_calls: 2, answered: 2, failed: 0, goal_achieved: 1 }];
const savedSummary = { timezone: zone, total_calls: 2, answered_calls: 2, failed_calls: 0,
    minutes_used: 37, minutes_remaining: 53, minutes_included: 90, minutes_state: "known",
    active_campaigns: 1, active_calls: 0, avg_call_duration_seconds: 60, queued_jobs: 0,
    outcome_breakdown: { answered: 2 } };
const extended = {
    getCallAnalytics: async (...args: unknown[]) => { calls.push({ method: "calls", args }); return { timezone: zone, series }; },
    getCallAnalyticsByCampaign: async (...args: unknown[]) => { calls.push({ method: "campaigns", args }); return { timezone: zone, campaigns: [] }; },
    getBestTimeToCall: async (...args: unknown[]) => { calls.push({ method: "best", args }); return {
        timezone: zone, best_hour: 9, hours: [{ hour: 9, total: 10, answered: 8, answer_rate: 0.8 }],
    }; },
    getRetryEffectiveness: async (...args: unknown[]) => { calls.push({ method: "retry", args }); return { timezone: zone, attempts: [] }; },
};
const dashboard = {
    getDashboardSummary: async (...args: unknown[]) => { calls.push({ method: "summary", args }); return summary(); },
    listCampaigns: async () => ({ campaigns: [] }),
};
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
// Keep actual page effects, data transforms and selection handlers. Only the
// unrelated visual shell/chart renderers and API transport ports are replaced.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
let loadingDashboard = false;
loader._load = function (id, ...args) {
    if (id.includes("lib/extended-api")) return { extendedApi: extended };
    if (id.includes("lib/dashboard-api")) return { dashboardApi: dashboard };
    if (id.includes("components/layout/dashboard-layout")) return {
        DashboardLayout: ({ children, description }: { children: React.ReactNode; description: string }) => <main><p>{description}</p>{children}</main>,
    };
    if (id.includes("components/providers/theme-provider")) return { useTheme: () => ({ theme: "light" }) };
    if (id.includes("components/ui/select")) return {
        Select: ({ value, onChange, ariaLabel, children }: { value: string; onChange: (value: string) => void; ariaLabel: string; children: React.ReactNode }) =>
            <select value={value} aria-label={ariaLabel} onChange={(event) => onChange(event.target.value)}>{children}</select>,
    };
    if (id === "framer-motion") return { motion, animate: () => ({ stop() {} }), useInView: () => false, useReducedMotion: () => true };
    if (id.includes("components/ui/dashboard-charts")) return {
        ActivityFeed: Nothing, AlertTimeline: Nothing, DonutChart: Nothing, Heatmap: Nothing,
        LiveCallsTimeSeriesChart: ({ timeZone }: { timeZone?: string }) => { chartZones.push(timeZone); return null; }, StackedAreaChart: Nothing,
    };
    if (id.includes("components/calls/call-issues-banner")) return { CallIssuesBanner: Nothing };
    if (id.includes("components/dashboard/first-call-setup")) return { FirstCallSetup: Nothing };
    if (loadingDashboard && id.includes("components/dashboard/dialer-insights")) return { DialerInsights: Nothing };
    return originalLoad.call(this, id, ...args);
};
let AnalyticsPage: React.ComponentType;
let DashboardPage: React.ComponentType;
let DialerInsights: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- bounded external ports above; actual pages are exercised
    AnalyticsPage = require("./page").default;
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- same scoped loader
    DialerInsights = require("../../components/dashboard/dialer-insights").DialerInsights;
    loadingDashboard = true;
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- same scoped loader
    DashboardPage = require("../dashboard/page").default;
} finally { loader._load = originalLoad; }

beforeEach(() => {
    calls = [];
    chartZones = [];
    summary = async () => savedSummary;
    mock.timers.enable({ apis: ["Date"], now: new Date("2026-10-31T22:30:00Z") });
    mock.method(globalThis, "fetch", async () => { throw new Error("Unexpected external request in reporting test"); });
    const resolved = Intl.DateTimeFormat.prototype.resolvedOptions;
    mock.method(Intl.DateTimeFormat.prototype, "resolvedOptions", function (this: Intl.DateTimeFormat) {
        return { ...resolved.call(this), timeZone: "America/Los_Angeles" };
    });
    // Date-only series labels must not be shifted into the browser's previous day.
    const formatDate = Date.prototype.toLocaleDateString;
    mock.method(Date.prototype, "toLocaleDateString", function (this: Date, locales?: Intl.LocalesArgument, options?: Intl.DateTimeFormatOptions) {
        return formatDate.call(this, locales, { timeZone: "America/Los_Angeles", ...options });
    });
    class Socket {
        static OPEN = 1;
        readyState = 0;
        onopen = null; onclose = null; onmessage = null; onerror = null;
        close() {} send() {}
    }
    socketDescriptor = Object.getOwnPropertyDescriptor(globalThis, "WebSocket");
    Object.defineProperty(globalThis, "WebSocket", { configurable: true, writable: true, value: Socket });
});
afterEach(() => {
    cleanup(); mock.restoreAll(); mock.timers.reset();
    if (socketDescriptor) Object.defineProperty(globalThis, "WebSocket", socketDescriptor);
    else Reflect.deleteProperty(globalThis, "WebSocket");
});

test("analytics initially lets the server resolve its stored zone and displays that effective zone", async () => {
    render(<AnalyticsPage />);
    await waitFor(() => assert.ok(calls.some((call) => call.method === "calls")));
    const first = calls.find((call) => call.method === "calls")!;
    assert.equal(first.args[0], undefined);
    assert.equal(first.args[1], undefined);
    assert.equal(first.args[5], undefined);
    await waitFor(() => assert.match(document.body.textContent ?? "", /Asia\/Karachi/));
});

test("changing the analytics range uses the returned zone's calendar day at a UTC month boundary", async () => {
    render(<AnalyticsPage />);
    await waitFor(() => assert.match(document.body.textContent ?? "", /Asia\/Karachi/));
    fireEvent.change(screen.getByRole("combobox", { name: "Select date range" }), { target: { value: "7" } });
    await waitFor(() => assert.ok(calls.some((call) => call.method === "calls" && call.args[0] === "2026-10-25")));
    const latest = calls.filter((call) => call.method === "calls").at(-1)!;
    assert.deepEqual(latest.args.slice(0, 2), ["2026-10-25", "2026-11-01"]);
    assert.equal(latest.args[5], zone);
    assert.match(document.body.textContent ?? "", /Nov 1/);
    assert.doesNotMatch(document.body.textContent ?? "", /Oct 31/);
});

test("dialer insights honors the stored zone instead of silently sending the browser's zone", async () => {
    render(<DialerInsights />);
    await waitFor(() => assert.match(document.body.textContent ?? "", /Asia\/Karachi/));
    assert.deepEqual(calls.find((call) => call.method === "best")?.args, []);
    assert.match(document.body.textContent ?? "", /9 AM/);
});

test("dashboard waits for the effective account zone before issuing local-month charts", async () => {
    let finish!: (value: unknown) => void;
    summary = () => new Promise((resolve) => { finish = resolve; });
    render(<DashboardPage />);
    try {
        await waitFor(() => assert.ok(calls.some((call) => call.method === "summary")));
        assert.equal(calls.filter((call) => ["calls", "campaigns"].includes(call.method)).length, 0);
    } finally { finish(savedSummary); }
    await waitFor(() => assert.ok(calls.some((call) => call.method === "campaigns")));
    const charts = calls.filter((call) => ["calls", "campaigns"].includes(call.method));
    assert.ok(charts.length >= 3);
    for (const chart of charts) {
        assert.deepEqual(chart.args.slice(0, 2), ["2026-11-01", "2026-11-01"]);
        assert.equal(chart.args[5], zone);
    }
    await waitFor(() => assert.match(document.body.textContent ?? "", /Asia\/Karachi/));
    await waitFor(() => assert.ok(chartZones.length > 0));
    assert.ok(chartZones.every((value) => value === zone));
});

test("unavailable account-zone bootstrap does not silently draw UTC charts", async () => {
    summary = async () => { throw new Error("Reporting timezone unavailable"); };
    render(<DashboardPage />);
    await waitFor(() => assert.match(screen.getByRole("alert").textContent ?? "", /Reporting timezone unavailable/));
    assert.equal(calls.filter((call) => ["calls", "campaigns"].includes(call.method)).length, 0);
});
