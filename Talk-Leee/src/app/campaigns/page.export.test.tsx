import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { api } from "@/lib/api";
import { dashboardApi, type Campaign } from "@/lib/dashboard-api";

// Plan 3.4 E4: the command bar's "Export campaigns" opens the campaigns
// Export window. Real page, real command bar, real table; only the layout
// and the two panels that fetch on their own are stubbed.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/campaigns/event-stream")) return { EventStream: nothing };
    if (id.includes("components/campaigns/alert-timeline")) return { AlertTimeline: nothing };
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
const requestAnimationFrame = window.requestAnimationFrame;
const cancelAnimationFrame = window.cancelAnimationFrame;
const clients: QueryClient[] = [];
const pushes: string[] = [];
const router = { push(href: string) { pushes.push(href); }, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } as unknown as React.ContextType<typeof AppRouterContext>;

afterEach(() => {
    cleanup();
    mock.restoreAll();
    clients.splice(0).forEach((client) => client.clear());
    window.requestAnimationFrame = requestAnimationFrame;
    window.cancelAnimationFrame = cancelAnimationFrame;
    localStorage.clear();
    pushes.splice(0);
});

const campaign = {
    id: "src-1", name: "Spring push", status: "running", direction: "outbound", system_prompt: "", voice_id: "voice-9",
    max_concurrent_calls: 7, total_leads: 250, calls_completed: 120, calls_failed: 9, created_at: "2026-01-01T00:00:00Z",
} as unknown as Campaign;

async function mountPage() {
    mock.method(api, "request", async () => ({ ready: true }));
    mock.method(dashboardApi, "listCampaigns", async () => ({ campaigns: [campaign] }));
    window.requestAnimationFrame = (callback) => window.setTimeout(() => callback(performance.now()), 0);
    window.cancelAnimationFrame = (id) => window.clearTimeout(id);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.push(client);
    render(<AppRouterContext.Provider value={router}><QueryClientProvider client={client}><Page /></QueryClientProvider></AppRouterContext.Provider>);
    await waitFor(() => assert.equal(screen.queryAllByRole("checkbox", { name: "Select Spring push" }).length, 1), { timeout: SLOW });
    await act(async () => { await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve())); });
    await act(async () => { await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve())); });
}

function exportWindow(): HTMLElement | null {
    return screen.queryByRole("dialog", { name: "Export & Reporting" });
}

test("the command bar's 'Export campaigns' opens the campaigns Export window", async () => {
    await mountPage();
    assert.equal(exportWindow() !== null, false);
    fireEvent.click(screen.getByRole("button", { name: "Ctrl + K" }));
    const input = within(screen.getByRole("dialog", { name: "Command Bar" })).getByRole("textbox");
    fireEvent.change(input, { target: { value: "/export" } });
    const result = screen.getByRole("button", { name: /Export campaigns/ });
    await act(async () => { fireEvent.click(result); });
    await waitFor(() => assert.equal(exportWindow() !== null, true), { timeout: SLOW });
    // The command bar closed and nothing was pushed to a #export hash that nobody reads.
    assert.equal(screen.queryByRole("dialog", { name: "Command Bar" }) !== null, false);
    assert.deepEqual(pushes, []);
    // The window is the real one: it lists the included rows.
    assert.equal(exportWindow()!.textContent?.includes("1 campaigns"), true);
    // Closing it with Escape hands the state back to the page.
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => assert.equal(exportWindow() !== null, false), { timeout: SLOW });
});

test("the command bar's 'Export campaigns' runs from the keyboard too", async () => {
    await mountPage();
    fireEvent.click(screen.getByRole("button", { name: "Ctrl + K" }));
    const input = within(screen.getByRole("dialog", { name: "Command Bar" })).getByRole("textbox");
    fireEvent.change(input, { target: { value: "/export campaigns" } });
    await act(async () => { fireEvent.keyDown(input, { key: "Enter" }); });
    await waitFor(() => assert.equal(exportWindow() !== null, true), { timeout: SLOW });
});
