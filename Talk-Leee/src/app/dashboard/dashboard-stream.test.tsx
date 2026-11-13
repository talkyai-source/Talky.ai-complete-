import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";

const baseline = { timezone: "Asia/Karachi", total_calls: 2, answered_calls: 2, failed_calls: 0,
    minutes_used: 37, minutes_remaining: 53, minutes_included: 90, minutes_state: "known",
    active_campaigns: 1, active_calls: 0, avg_call_duration_seconds: 60, queued_jobs: 0,
    outcome_breakdown: { answered: 2 } };
let summary = baseline;
let requests: { method: string; args: unknown[] }[] = [];
const dashboard = {
    getDashboardSummary: async (...args: unknown[]) => { requests.push({ method: "summary", args }); return summary; },
    listCampaigns: async () => ({ campaigns: [] }),
};
const extended = {
    getCallAnalytics: async (...args: unknown[]) => { requests.push({ method: "analytics", args }); return { timezone: baseline.timezone, series: [] }; },
    getCallAnalyticsByCampaign: async () => ({ timezone: baseline.timezone, campaigns: [] }),
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
// Mount the real page, effects and polling. Replace only external API ports and
// unrelated visual children, then immediately restore the module loader.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id.includes("lib/extended-api")) return { extendedApi: extended };
    if (id.includes("lib/dashboard-api")) return { dashboardApi: dashboard };
    if (id.includes("components/layout/dashboard-layout")) return {
        DashboardLayout: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
    };
    if (id === "framer-motion") return { motion, animate: () => ({ stop() {} }), useInView: () => false, useReducedMotion: () => true };
    if (id.includes("components/ui/dashboard-charts")) return {
        ActivityFeed: Nothing, AlertTimeline: Nothing, DonutChart: Nothing, Heatmap: Nothing,
        LiveCallsTimeSeriesChart: Nothing, StackedAreaChart: Nothing,
    };
    if (id.includes("components/calls/call-issues-banner")) return { CallIssuesBanner: Nothing };
    if (id.includes("components/dashboard/first-call-setup")) return { FirstCallSetup: Nothing };
    if (id.includes("components/dashboard/dialer-insights")) return { DialerInsights: Nothing };
    return originalLoad.call(this, id, ...args);
};
let DashboardPage: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- bounded external ports above; actual page is exercised
    DashboardPage = require("./page").default;
} finally { loader._load = originalLoad; }

type Timer = { delay: number; callback: () => void };
let intervals: Map<number, Timer>;
let timeouts: Map<number, Timer>;
let timerId = 0;
let sockets: FakeSocket[];
let externalRequests: unknown[];
let socketDescriptor: PropertyDescriptor | undefined;
let frameDescriptor: PropertyDescriptor | undefined;
let cancelFrameDescriptor: PropertyDescriptor | undefined;
const originalUrl = process.env.NEXT_PUBLIC_DASHBOARD_WS_URL;
class FakeSocket {
    static OPEN = 1;
    readyState = 0;
    onopen: (() => void) | null = null;
    onclose: (() => void) | null = null;
    onmessage: ((event: { data: string }) => void) | null = null;
    onerror: (() => void) | null = null;
    sent: string[] = [];
    closed = false;
    constructor(readonly url: string) { sockets.push(this); }
    send(value: string) { this.sent.push(value); }
    close() { this.closed = true; this.readyState = 3; }
    open() { this.readyState = FakeSocket.OPEN; this.onopen?.(); }
    peerClose() { this.readyState = 3; this.onclose?.(); }
    message(value: unknown) { this.onmessage?.({ data: JSON.stringify(value) }); }
}
async function tickIntervals(delay: number) {
    await act(async () => {
        for (const timer of [...intervals.values()]) if (timer.delay === delay) timer.callback();
    });
}
async function ready() {
    await screen.findByLabelText("Minutes usage: used 37, total 90 min, remaining 53");
    await waitFor(() => assert.ok(requests.some(request => request.method === "analytics")));
}
beforeEach(() => {
    delete process.env.NEXT_PUBLIC_DASHBOARD_WS_URL;
    summary = { ...baseline };
    requests = []; sockets = []; externalRequests = [];
    intervals = new Map(); timeouts = new Map(); timerId = 0;
    socketDescriptor = Object.getOwnPropertyDescriptor(globalThis, "WebSocket");
    frameDescriptor = Object.getOwnPropertyDescriptor(window, "requestAnimationFrame");
    cancelFrameDescriptor = Object.getOwnPropertyDescriptor(window, "cancelAnimationFrame");
    Object.defineProperty(globalThis, "WebSocket", { configurable: true, writable: true, value: FakeSocket });
    // JSDOM need not provide animation frames. Flush the page's frame commit
    // synchronously inside act; there is no pending frame for cancel to retire.
    Object.defineProperty(window, "requestAnimationFrame", { configurable: true, writable: true,
        value: (callback: FrameRequestCallback) => { callback(0); return 1; } });
    Object.defineProperty(window, "cancelAnimationFrame", { configurable: true, writable: true, value: () => undefined });
    mock.method(globalThis, "fetch", async (...args: unknown[]) => {
        externalRequests.push(args);
        throw new Error("Unexpected external request in dashboard stream test");
    });
    // Keep global/RTL wait timers live. Only the page's window-owned schedules
    // are controlled, so a poll tick does not require 30 seconds of wall time.
    mock.method(window, "setInterval", (handler: TimerHandler, delay?: number) => {
        assert.equal(typeof handler, "function");
        const id = ++timerId;
        intervals.set(id, { delay: delay ?? 0, callback: handler as () => void });
        return id;
    });
    mock.method(window, "clearInterval", (id?: number) => { if (id !== undefined) intervals.delete(id); });
    mock.method(window, "setTimeout", (handler: TimerHandler, delay?: number) => {
        assert.equal(typeof handler, "function");
        const id = ++timerId;
        timeouts.set(id, { delay: delay ?? 0, callback: handler as () => void });
        return id;
    });
    mock.method(window, "clearTimeout", (id?: number) => { if (id !== undefined) timeouts.delete(id); });
});
afterEach(() => {
    cleanup();
    mock.restoreAll();
    if (socketDescriptor) Object.defineProperty(globalThis, "WebSocket", socketDescriptor);
    else Reflect.deleteProperty(globalThis, "WebSocket");
    if (frameDescriptor) Object.defineProperty(window, "requestAnimationFrame", frameDescriptor);
    else Reflect.deleteProperty(window, "requestAnimationFrame");
    if (cancelFrameDescriptor) Object.defineProperty(window, "cancelAnimationFrame", cancelFrameDescriptor);
    else Reflect.deleteProperty(window, "cancelAnimationFrame");
    if (originalUrl === undefined) delete process.env.NEXT_PUBLIC_DASHBOARD_WS_URL;
    else process.env.NEXT_PUBLIC_DASHBOARD_WS_URL = originalUrl;
    assert.deepEqual(externalRequests, [], "A swallowed fetch error must not conceal a real transport attempt");
});

for (const value of [undefined, "   "]) test(`dashboard without an explicit stream URL (${value === undefined ? "unset" : "blank"}) never opens a socket`, async () => {
    if (value !== undefined) process.env.NEXT_PUBLIC_DASHBOARD_WS_URL = value;
    render(<DashboardPage />);
    await ready();
    assert.deepEqual(sockets.map(socket => socket.url), [], "Never invent a /ws endpoint on the frontend origin");
    assert.equal(timeouts.size, 0, "No unconfigured stream reconnect loop");
});

test("an unconfigured dashboard keeps its REST summary poll and releases schedules on unmount", async () => {
    const view = render(<DashboardPage />);
    await ready();
    assert.equal(requests.filter(request => request.method === "summary").length, 1);
    summary = { ...baseline, minutes_used: 41, minutes_remaining: 49 };
    await tickIntervals(30_000);
    assert.equal(requests.filter(request => request.method === "summary").length, 2);
    assert.deepEqual(requests.filter(request => request.method === "summary").at(-1)?.args, [baseline.timezone]);
    await screen.findByLabelText("Minutes usage: used 41, total 90 min, remaining 49");
    view.unmount();
    assert.equal(intervals.size, 0);
    assert.equal(timeouts.size, 0);
});

test("an explicitly configured dashboard stream still connects, consumes summaries and sends heartbeats", async () => {
    process.env.NEXT_PUBLIC_DASHBOARD_WS_URL = " wss://dashboard-stream.example.invalid/live ";
    const view = render(<DashboardPage />);
    await ready();
    assert.deepEqual(sockets.map(socket => socket.url), ["wss://dashboard-stream.example.invalid/live"]);
    act(() => { sockets[0].open(); });
    await tickIntervals(30_000);
    assert.equal(requests.filter(request => request.method === "summary").length, 1, "Connected stream does not also poll the summary");
    act(() => { sockets[0].message({ ts: Date.now(), summary: { ...baseline, minutes_used: 41, minutes_remaining: 49 } }); });
    await tickIntervals(1_000);
    await screen.findByLabelText("Minutes usage: used 41, total 90 min, remaining 49");
    assert.equal(JSON.parse(sockets[0].sent[0]).type, "ping");
    view.unmount();
    assert.equal(sockets[0].closed, true);
    assert.equal(intervals.size, 0);
    assert.equal(timeouts.size, 0);
});

test("a configured stream failure retains the existing REST fallback and cleans its retry", async () => {
    process.env.NEXT_PUBLIC_DASHBOARD_WS_URL = "wss://dashboard-stream.example.invalid/live";
    const view = render(<DashboardPage />);
    await ready();
    act(() => { sockets[0].open(); sockets[0].peerClose(); });
    assert.equal(timeouts.size, 1, "Configured streams retain their existing bounded reconnect schedule");
    summary = { ...baseline, minutes_used: 41, minutes_remaining: 49 };
    await tickIntervals(30_000);
    await screen.findByLabelText("Minutes usage: used 41, total 90 min, remaining 49");
    assert.equal(requests.filter(request => request.method === "summary").length, 2);
    view.unmount();
    assert.equal(intervals.size, 0);
    assert.equal(timeouts.size, 0);
});
