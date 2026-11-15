/**
 * FX5 Task B2 — the campaigns page shows nothing made up: the alert timeline
 * invents no "Investigating" step and no prototype text, and the command bar
 * offers no prototype entries. Real server alerts (and their real status,
 * including a real "Investigating" one) stay as they are.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import type { AlertItem } from "@/lib/campaign-performance";
import type { Campaign } from "@/lib/dashboard-api";

let alerts: AlertItem[] = [];
const idle = { mutate() {}, mutateAsync: async () => undefined, isPending: false };

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id.endsWith("lib/alerts-api")) return { useAlerts: () => ({ data: alerts, isLoading: false, isError: false, error: null }), useAckAlert: () => idle, useResolveAlert: () => idle };
    if (id.includes("components/campaigns/campaign-readiness")) return { CampaignReadinessNotice: () => null, useCampaignReadiness: () => ({ ready: true, reason: "" }) };
    return originalLoad.call(this, id, ...args);
};
let AlertTimeline: React.ComponentType<{ campaigns: Campaign[] }>;
let CommandBar: React.ComponentType<{ campaigns: Campaign[]; onPause: (c: Campaign[]) => Promise<void>; onResume: (ids: string[]) => void; onExport: () => void }>;
try {
    /* eslint-disable @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above */
    AlertTimeline = require("./alert-timeline").AlertTimeline;
    CommandBar = require("./command-bar").CommandBar;
    /* eslint-enable @typescript-eslint/no-require-imports */
} finally {
    loader._load = originalLoad;
}

const router = { push() {}, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } as unknown as React.ContextType<typeof AppRouterContext>;
const originalRaf = window.requestAnimationFrame;
const originalCancelRaf = window.cancelAnimationFrame;
afterEach(() => {
    cleanup();
    alerts = [];
    localStorage.clear();
    window.requestAnimationFrame = originalRaf;
    window.cancelAnimationFrame = originalCancelRaf;
});
const shown = (text: string | RegExp) => screen.queryByText(text) !== null;

const ALERT: AlertItem = {
    id: "alert-1", title: "Carrier errors rising", description: "Outbound attempts are failing.", severity: "Critical", type: "Network",
    status: "Active", createdAt: "2026-11-10T10:00:00Z", updatedAt: "2026-11-10T10:30:00Z", acknowledged: false,
    relatedCampaignIds: [], metadata: { failed_calls: 12 },
} as AlertItem;

function mountTimeline(items: AlertItem[]) {
    alerts = items;
    window.requestAnimationFrame = ((callback: FrameRequestCallback) => window.setTimeout(() => callback(0), 0)) as typeof window.requestAnimationFrame;
    window.cancelAnimationFrame = ((handle: number) => window.clearTimeout(handle)) as typeof window.cancelAnimationFrame;
    render(<AlertTimeline campaigns={[]} />);
}

test("alert timeline: an Active alert's timeline has no invented 'Investigating' step", () => {
    mountTimeline([ALERT]);
    fireEvent.click(screen.getByRole("button", { name: /Carrier errors rising/ }));
    fireEvent.click(screen.getByRole("button", { name: "Timeline Visualization" }));
    const dialog = screen.getByRole("dialog");
    assert.equal(within(dialog).queryByText("Detected") !== null, true, "the real created time stays");
    assert.equal(within(dialog).queryByText("Latest update") !== null, true, "the real updated time stays");
    assert.equal(within(dialog).queryByText("Investigating") === null, true, "no step the server never reported");
});

test("alert timeline: no prototype text and no empty 'Related Incidents' part", () => {
    mountTimeline([ALERT]);
    fireEvent.click(screen.getByRole("button", { name: /Carrier errors rising/ }));
    assert.equal(screen.queryByRole("button", { name: "Related Incidents" }) === null, true);
    fireEvent.click(screen.getByRole("button", { name: "Root Cause" }));
    assert.equal(shown(/Prototype/i), false);
    assert.equal(shown("failed_calls"), true, "the server's own metadata stays");
    for (const tab of ["Impact Analysis", "Timeline Visualization", "Recommended Actions"]) {
        fireEvent.click(screen.getByRole("button", { name: tab }));
        assert.equal(shown(/Prototype/i), false, tab);
    }
});

test("alert timeline: a real server status of Investigating is still shown on its alert", () => {
    mountTimeline([{ ...ALERT, id: "alert-2", title: "Queue backlog", status: "Investigating", acknowledged: true } as AlertItem]);
    const row = screen.getByRole("button", { name: /Queue backlog/ });
    assert.equal(within(row).queryByText("Investigating") !== null, true);
});

function mountBar() {
    const paused: Campaign[][] = [];
    const campaigns = [{ id: "camp-1", name: "Holiday push", status: "running" }] as unknown as Campaign[];
    render(
        <AppRouterContext.Provider value={router}>
            <CommandBar campaigns={campaigns} onPause={async (list) => { paused.push(list); }} onResume={() => {}} onExport={() => {}} />
        </AppRouterContext.Provider>,
    );
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const box = within(screen.getByRole("dialog")).getByRole("textbox") as HTMLInputElement;
    return { box, paused };
}

test("command bar: the real pause action is offered without the 'Prototype action' line", () => {
    const { box } = mountBar();
    fireEvent.change(box, { target: { value: "/" } });
    assert.equal(shown("Pause all active campaigns"), true, "the real action stays");
    assert.equal(shown("Prototype action"), false);
    assert.equal(shown(/prototype/i), false);
});

test("command bar: '#' offers no made-up tag filters and the hint no longer lists tags", () => {
    const { box } = mountBar();
    fireEvent.change(box, { target: { value: "#" } });
    assert.equal(shown("Filter view (prototype)"), false);
    for (const tag of ["#priority", "#sales", "#support", "#survey"]) assert.equal(shown(tag), false, tag);
    assert.equal(shown("No results"), true);
    const hint = screen.getByText(/Prefixes:/);
    assert.equal(/tags/.test(hint.textContent ?? ""), false);
    assert.equal(/actions/.test(hint.textContent ?? "") && /navigation/.test(hint.textContent ?? ""), true, "the real prefixes stay");
});

test("command bar: '@' offers no made-up people and the hint no longer lists users", () => {
    const { box } = mountBar();
    fireEvent.change(box, { target: { value: "@" } });
    for (const name of ["Alex Operator", "Morgan Analyst", "Jamie Admin"]) assert.equal(shown(name), false, name);
    assert.equal(shown("No results"), true);
    const hint = screen.getByText(/Prefixes:/);
    assert.equal(/users|@/.test(hint.textContent ?? ""), false);
    assert.equal(/actions/.test(hint.textContent ?? "") && /navigation/.test(hint.textContent ?? ""), true, "the real prefixes stay");
});

test("alert timeline: Recommended Actions offers no rule to create while the rule builder is off", () => {
    mountTimeline([ALERT]);
    fireEvent.click(screen.getByRole("button", { name: /Carrier errors rising/ }));
    fireEvent.click(screen.getByRole("button", { name: "Recommended Actions" }));
    const dialog = screen.getByRole("dialog");
    assert.equal(within(dialog).queryByText(/Create a rule/) === null, true);
    for (const kept of [
        "Acknowledge the alert and assign an owner.",
        "Inspect network/API error codes and recent deployment changes.",
        "Apply targeted retries or lower concurrency temporarily.",
    ]) assert.equal(within(dialog).queryByText(kept) !== null, true, kept);
});
