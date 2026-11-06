import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ApiClientError } from "@/lib/http-client";
import type { Campaign } from "@/lib/dashboard-api";

const RAW = "Synthetic private diagnostic: SELECT internal_row FROM unavailable_store";
const REQUEST = "qa-read-reference-7";
const campaign: Campaign = { id: "aabbccdd-1234-4abc-8def-0123456789ab", name: "Last loaded campaign",
    direction: "outbound", status: "draft", total_leads: 7, calls_completed: 3, calls_failed: 1,
    system_prompt: "Synthetic campaign instructions", voice_id: "synthetic-voice", max_concurrent_calls: 1,
    created_at: "2026-11-06T12:00:00Z" };
const analytics = { timezone: "Asia/Karachi", series: [
    { date: "2026-11-01", total_calls: 7, answered: 5, failed: 2, goal_achieved: 2 },
] };
let campaignRead: () => Promise<Campaign[]>;
let analyticsRead: () => Promise<typeof analytics>;
let campaignReads = 0, analyticsReads = 0, mutationCalls = 0, networkCalls = 0;
let online = true;
let refreshCampaigns: () => Promise<unknown>;
const frameDescriptor = Object.getOwnPropertyDescriptor(window, "requestAnimationFrame");
const cancelFrameDescriptor = Object.getOwnPropertyDescriptor(window, "cancelAnimationFrame");

function failure() {
    return new ApiClientError({ code: "server_error", message: RAW, status: 503,
        url: "/synthetic-read", method: "GET", requestId: REQUEST });
}

// This port models an ordinary query read/refetch and retains cached data on
// read failure. The actual campaign page and actual performance table render.
function useCampaignRead() {
    const [state, setState] = React.useState<{ data?: Campaign[]; error?: unknown; isLoading: boolean; isError: boolean }>({ isLoading: true, isError: false });
    const refetch = React.useCallback(async () => {
        campaignReads += 1;
        try {
            const data = await campaignRead();
            setState({ data, isLoading: false, isError: false });
            return { data };
        } catch (error) {
            setState(previous => ({ ...previous, error, isLoading: false, isError: true }));
            return { error };
        }
    }, []);
    React.useEffect(() => { refreshCampaigns = refetch; void refetch(); }, [refetch]);
    return { ...state, refetch };
}

const Nothing = () => null;
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { useRouter: () => ({ push() {}, back() {} }) };
    if (id.includes("components/layout/dashboard-layout")) return {
        DashboardLayout: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
    };
    if (id.endsWith("components/providers/theme-provider")) return { useTheme: () => ({ theme: "light" }) };
    if (id.endsWith("lib/extended-api")) return {
        extendedApi: { getCallAnalytics: async () => { analyticsReads += 1; return analyticsRead(); } },
    };
    if (id.endsWith("lib/api-hooks")) return { useOutboundCampaigns: useCampaignRead, queryKeys: { campaigns: () => ["campaigns"] } };
    if (id === "@tanstack/react-query") return { useQueryClient: () => ({ setQueryData() {}, invalidateQueries: async () => undefined }) };
    if (id.endsWith("lib/notification-mutations")) return {
        useNotificationMutation: () => ({ mutateAsync: async () => { mutationCalls += 1; throw new Error("Read retry must never replay a mutation"); } }),
    };
    if (id.endsWith("lib/notifications-client")) return { useNotificationsActions: () => ({ create() {} }) };
    for (const [path, name] of [["event-stream", "EventStream"], ["alert-timeline", "AlertTimeline"],
        ["command-bar", "CommandBar"], ["campaign-readiness", "CampaignStartControl"]]) {
        if (id.endsWith(`components/campaigns/${path}`)) return { [name]: Nothing };
    }
    return originalLoad.call(this, id, ...args);
};
let AnalyticsPage: React.ComponentType, CampaignsPage: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- actual pages with only external query/shell ports replaced
    AnalyticsPage = require("../../app/analytics/page").default;
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- actual page and actual table, not an error-view stand-in
    CampaignsPage = require("../../app/campaigns/page").default;
} finally { loader._load = originalLoad; }

beforeEach(() => {
    online = true;
    campaignReads = analyticsReads = mutationCalls = networkCalls = 0;
    campaignRead = async () => { throw failure(); };
    analyticsRead = async () => { throw failure(); };
    window.localStorage.clear();
    Object.defineProperty(window, "requestAnimationFrame", { configurable: true, writable: true,
        value: (callback: FrameRequestCallback) => window.setTimeout(() => callback(performance.now()), 0) });
    Object.defineProperty(window, "cancelAnimationFrame", { configurable: true, writable: true,
        value: (id: number) => window.clearTimeout(id) });
    mock.getter(Object.getPrototypeOf(window.navigator), "onLine", () => online);
    mock.method(window.HTMLCanvasElement.prototype, "getContext", () => null);
    mock.method(globalThis, "fetch", async () => { networkCalls += 1; throw new Error("No live network in read recovery tests"); });
});
afterEach(() => {
    cleanup(); mock.restoreAll(); window.localStorage.clear();
    for (const [key, descriptor] of [["requestAnimationFrame", frameDescriptor], ["cancelAnimationFrame", cancelFrameDescriptor]] as const) {
        if (descriptor) Object.defineProperty(window, key, descriptor);
        else Reflect.deleteProperty(window, key);
    }
});

function readCount(kind: "analytics" | "campaigns") { return kind === "analytics" ? analyticsReads : campaignReads; }
function succeed(kind: "analytics" | "campaigns") {
    if (kind === "analytics") analyticsRead = async () => analytics;
    else campaignRead = async () => [campaign];
}
function fail(kind: "analytics" | "campaigns") {
    if (kind === "analytics") analyticsRead = async () => { throw failure(); };
    else campaignRead = async () => { throw failure(); };
}
async function loaded(kind: "analytics" | "campaigns") {
    await waitFor(() => {
        if (kind === "campaigns") assert.ok(screen.getByText(campaign.name));
        else assert.match(screen.getByText("Total Calls").parentElement?.textContent ?? "", /7/);
    });
}
function noWriteOrNetwork() { assert.equal(mutationCalls, 0); assert.equal(networkCalls, 0); }

for (const [kind, Page] of [["analytics", AnalyticsPage], ["campaigns", CampaignsPage]] as const) {
    test(`${kind} read failure shows safe copy and request reference, never raw details or empty success`, async () => {
        render(<Page />);
        const alert = await screen.findByRole("alert");
        assert.match(alert.textContent ?? "", /could not load|couldn't load/i);
        assert.ok(alert.textContent?.includes(REQUEST));
        assert.equal(screen.queryByText(RAW), null);
        assert.equal(screen.queryByText(/0-0 of 0/), null);
        assert.equal(screen.queryByText(/No campaigns match|No data|Create a campaign first/i), null);
        assert.ok(within(alert).getByRole("button", { name: /try again|retry/i }));
        assert.equal(readCount(kind), 1);
        noWriteOrNetwork();
    });

    test(`${kind} retry is explicit, repeats only the failed read and restores the loaded view`, async () => {
        render(<Page />);
        const alert = await screen.findByRole("alert");
        succeed(kind);
        fireEvent.click(within(alert).getByRole("button", { name: /try again|retry/i }));
        await loaded(kind);
        assert.equal(readCount(kind), 2);
        assert.equal(screen.queryByRole("alert"), null);
        noWriteOrNetwork();
    });

    test(`${kind} offline read state updates on reconnect but waits for an explicit retry`, async () => {
        online = false;
        render(<Page />);
        const alert = await screen.findByRole("alert");
        assert.match(alert.textContent ?? "", /offline/i);
        assert.doesNotMatch(alert.textContent ?? "", /nothing was saved|nothing saved/i);
        succeed(kind);
        await act(async () => { online = true; window.dispatchEvent(new Event("online")); });
        assert.equal(readCount(kind), 1, "An online event must not replay work automatically");
        fireEvent.click(within(screen.getByRole("alert")).getByRole("button", { name: /try again|retry/i }));
        await loaded(kind);
        assert.equal(readCount(kind), 2);
        noWriteOrNetwork();
    });

    test(`${kind} failed refresh retains last loaded data with an explicit stale-data notice`, async () => {
        succeed(kind);
        render(<Page />);
        await loaded(kind);
        fail(kind);
        if (kind === "campaigns") await act(async () => { await refreshCampaigns(); });
        else fireEvent.click(screen.getByRole("button", { name: "Week" }));
        const alert = await screen.findByRole("alert");
        assert.match(alert.textContent ?? "", /last loaded data/i);
        await loaded(kind);
        assert.equal(readCount(kind), 2);
        assert.equal(screen.queryByText(/0-0 of 0|No campaigns match|No data/i), null);
        noWriteOrNetwork();
    });
}
