/**
 * FX3a Task 2 — text and labels on the Campaigns list: the page title, the
 * create button, the details window's buttons and the Start window's wording.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { api } from "@/lib/api";
import { dashboardApi, type Campaign } from "@/lib/dashboard-api";

// Same boundary stubs as page.start.test.tsx, except that the layout shows its title.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) {
        return { DashboardLayout: ({ title, children }: { title?: string; children: React.ReactNode }) => <><h1>{title}</h1>{children}</> };
    }
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
const router = { push() {}, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } as unknown as React.ContextType<typeof AppRouterContext>;

afterEach(() => {
    cleanup();
    mock.restoreAll();
    clients.splice(0).forEach((client) => client.clear());
    window.requestAnimationFrame = requestAnimationFrame;
    window.cancelAnimationFrame = cancelAnimationFrame;
    localStorage.clear();
});

const paused = {
    id: "p1", name: "Paused one", description: "", status: "paused", direction: "outbound", system_prompt: "", voice_id: "v",
    max_concurrent_calls: 1, total_leads: 10, calls_completed: 0, calls_failed: 0, created_at: "2026-01-01T00:00:00Z",
} as unknown as Campaign;

function rowBox() { return screen.queryByRole("checkbox", { name: "Select Paused one" }); }

async function mountPage() {
    window.requestAnimationFrame = (callback) => window.setTimeout(() => callback(performance.now()), 0);
    window.cancelAnimationFrame = (id) => window.clearTimeout(id);
    mock.method(api, "request", async () => ({ ready: true }));
    mock.method(api, "getCampaignKnowledge", async (id: string) => ({ campaign_id: id, knowledge_mode: "none", sources: [], tree: [] }));
    mock.method(dashboardApi, "listCampaigns", async () => ({ campaigns: [paused] }));
    mock.method(dashboardApi, "getMinutesStatus", async () => ({ allocated: 100, used_minutes: 10, remaining_minutes: 90, unlimited: false, exhausted: false }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.push(client);
    render(<AppRouterContext.Provider value={router}><QueryClientProvider client={client}><Page /></QueryClientProvider></AppRouterContext.Provider>);
    await waitFor(() => assert.equal(rowBox() !== null, true), { timeout: SLOW });
    await act(async () => { await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve())); });
    await act(async () => { await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve())); });
}

function openDetails() {
    const row = rowBox()!.closest("[role='row']") as HTMLElement;
    fireEvent.click(within(row).getByText("Paused one").closest("button")!);
    return within(screen.getByRole("dialog", { name: "Paused one" }));
}

test("FX3a: the page title is the menu name, and the create button says Create campaign", async () => {
    await mountPage();
    assert.equal(screen.queryByRole("heading", { level: 1, name: "Campaigns" }) !== null, true);
    assert.equal(screen.queryByText("Campaign Performance") !== null, false);
    assert.equal(screen.queryByRole("link", { name: "Create campaign" }) !== null, true);
    assert.equal(screen.queryByRole("link", { name: "New Campaign" }) !== null, false);
});

test("FX3a: the details window says Open page and Edit", async () => {
    await mountPage();
    const details = openDetails();
    assert.equal(details.queryByRole("link", { name: "Open page" }) !== null, true);
    assert.equal(details.queryByRole("link", { name: "Open Page" }) !== null, false);
    assert.equal(details.queryByRole("button", { name: "Edit" }) !== null, true);
    assert.equal(details.queryByRole("button", { name: "Edit Campaign" }) !== null, false);
});

test("FX3a: the Start window calls the person being called the callee everywhere", async () => {
    await mountPage();
    const details = openDetails();
    await waitFor(() => assert.equal((details.getByRole("button", { name: "Resume" }) as HTMLButtonElement).disabled, false), { timeout: SLOW });
    fireEvent.click(details.getByRole("button", { name: "Resume" }));
    await waitFor(() => assert.equal(screen.queryByRole("dialog", { name: "Who speaks first?" }) !== null, true), { timeout: SLOW });
    const dialog = screen.getByRole("dialog", { name: "Who speaks first?" });
    assert.equal(within(dialog).queryByRole("radio", { name: /Callee speaks first/ }) !== null, true);
    assert.equal(within(dialog).queryByRole("radio", { name: /Caller speaks first/ }) !== null, false);
    const text = dialog.textContent ?? "";
    assert.equal(text.includes("waits for the callee."), true);
    assert.equal(/\bcaller\b/i.test(text), false);
});
