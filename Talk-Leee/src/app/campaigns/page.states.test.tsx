/**
 * FX2a Task 1.1 + 1.7 — the Campaigns list uses the shared loading / error /
 * empty states, Retry reads the list again, and a refused delete shows the
 * server's message in the delete dialog without an unhandled error.
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
import { ApiClientError } from "@/lib/http-client";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/campaigns/event-stream")) return { EventStream: nothing };
    if (id.includes("components/campaigns/alert-timeline")) return { AlertTimeline: nothing };
    if (id.includes("components/campaigns/command-bar")) return { CommandBar: nothing };
    if (id === "next/link") {
        return { __esModule: true, default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> };
    }
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
const shown = (text: string | RegExp) => screen.queryByText(text) !== null;
const requestAnimationFrame = window.requestAnimationFrame;
const cancelAnimationFrame = window.cancelAnimationFrame;
const clients: QueryClient[] = [];
const router = { push() {}, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } as unknown as React.ContextType<typeof AppRouterContext>;
const unhandled: unknown[] = [];
const onUnhandled = (reason: unknown) => { unhandled.push(reason); };

afterEach(() => {
    cleanup();
    mock.restoreAll();
    clients.splice(0).forEach((client) => client.clear());
    window.requestAnimationFrame = requestAnimationFrame;
    window.cancelAnimationFrame = cancelAnimationFrame;
    process.off("unhandledRejection", onUnhandled);
    unhandled.length = 0;
    localStorage.clear();
});

const campaign = {
    id: "c-1", name: "Spring push", description: "Q2 outreach", status: "paused", direction: "outbound",
    system_prompt: "", voice_id: "voice-9", max_concurrent_calls: 7,
    total_leads: 250, calls_completed: 120, calls_failed: 9, created_at: "2026-01-01T00:00:00Z",
    script_config: { persona_type: "lead_gen", company_name: "Acme", agent_names: ["Ava"], campaign_slots: {} },
} as unknown as Campaign;

function mountPage() {
    window.requestAnimationFrame = (callback) => window.setTimeout(() => callback(performance.now()), 0);
    window.cancelAnimationFrame = (id) => window.clearTimeout(id);
    mock.method(api, "request", async () => ({ ready: true }));
    mock.method(api, "getCampaignKnowledge", async (id: string) => ({ campaign_id: id, knowledge_mode: "none", sources: [], tree: [] }));
    // Mutations too: a finished mutation is otherwise kept for 5 minutes and holds the process open.
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { gcTime: Infinity } } });
    clients.push(client);
    render(<AppRouterContext.Provider value={router}><QueryClientProvider client={client}><Page /></QueryClientProvider></AppRouterContext.Provider>);
}
const frames = async () => {
    await act(async () => { await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve())); });
    await act(async () => { await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve())); });
};

test("while the list is being read the shared loading state is announced as a status", async () => {
    let answer!: (value: { campaigns: Campaign[] }) => void;
    mock.method(dashboardApi, "listCampaigns", () => new Promise<{ campaigns: Campaign[] }>((resolve) => { answer = resolve; }));
    mountPage();
    const status = await screen.findByRole("status");
    assert.equal(within(status).queryByText("Loading") !== null, true, "the shared LoadingState");
    await act(async () => { answer({ campaigns: [campaign] }); });
    await waitFor(() => assert.equal(screen.queryAllByRole("checkbox", { name: "Select Spring push" }).length, 1), { timeout: SLOW });
    assert.equal(screen.queryByRole("status") === null || !shown("Loading"), true);
});

test("a failed list read shows the server's message in the shared error state, and Retry reads the list again", async () => {
    let fail = true;
    const read = mock.method(dashboardApi, "listCampaigns", async () => {
        if (fail) throw new Error("Campaign store is unavailable");
        return { campaigns: [campaign] };
    });
    mountPage();
    await waitFor(() => assert.equal(shown("Could not load campaigns."), true), { timeout: SLOW });
    assert.equal(shown("Something went wrong"), true, "the shared ErrorState");
    assert.equal(screen.getByText("Could not load campaigns.").getAttribute("role"), "alert");
    assert.equal(shown("No campaigns match your filters"), false);
    assert.equal(shown("No campaigns yet"), false);

    fail = false;
    const before = read.mock.callCount();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => assert.equal(screen.queryAllByRole("checkbox", { name: "Select Spring push" }).length, 1), { timeout: SLOW });
    assert.equal(read.mock.callCount() > before, true, "Retry sent the request again");
    assert.equal(shown("Could not load campaigns."), false);
});

test("an answer without a campaigns list is a failed read: the error state, not an empty list and not a crash", async () => {
    mock.method(dashboardApi, "listCampaigns", async () => ({}) as unknown as { campaigns: Campaign[] });
    mountPage();
    await waitFor(() => assert.equal(shown("Could not load campaigns."), true), { timeout: SLOW });
    assert.equal(screen.queryByRole("button", { name: "Retry" }) !== null, true);
    assert.equal(shown("No campaigns yet"), false);
    assert.equal(shown("No campaigns match your filters"), false);
});

test("with no campaigns at all the list reads 'No campaigns yet', not 'No campaigns match your filters'", async () => {
    mock.method(dashboardApi, "listCampaigns", async () => ({ campaigns: [] }));
    mountPage();
    await waitFor(() => assert.equal(shown("No campaigns yet"), true), { timeout: SLOW });
    assert.equal(shown("No campaigns match your filters"), false);
    // FX2a2: only the existing "No campaigns yet" — no link and no description line.
    const title = screen.getByRole("heading", { name: "No campaigns yet" });
    assert.equal(screen.queryByRole("link", { name: "Create your first campaign" }) === null, true);
    assert.equal(title.parentElement!.querySelector("p") === null, true, "no description line, not even an empty one");
});

test("when filters hide every campaign the list reads 'No campaigns match your filters'", async () => {
    mock.method(dashboardApi, "listCampaigns", async () => ({ campaigns: [campaign] }));
    mountPage();
    await waitFor(() => assert.equal(screen.queryAllByRole("checkbox", { name: "Select Spring push" }).length, 1), { timeout: SLOW });
    await frames();
    fireEvent.change(screen.getByLabelText("Minimum success rate"), { target: { value: "100" } });
    await waitFor(() => assert.equal(shown("No campaigns match your filters"), true), { timeout: SLOW });
    assert.equal(shown("Try adjusting status or success rate."), true);
    assert.equal(shown("No campaigns yet"), false);
});

test("a refused delete shows the server's message in the delete dialog and leaves no unhandled error", async () => {
    process.on("unhandledRejection", onUnhandled);
    mock.method(dashboardApi, "listCampaigns", async () => ({ campaigns: [campaign] }));
    const remove = mock.method(dashboardApi, "deleteCampaign", async () => {
        throw new Error("Stop the campaign before deleting it");
    });
    mountPage();
    await waitFor(() => assert.equal(screen.queryAllByRole("checkbox", { name: "Select Spring push" }).length, 1), { timeout: SLOW });
    await frames();
    const row = screen.getByRole("checkbox", { name: "Select Spring push" }).closest("[role='row']") as HTMLElement;
    fireEvent.click(within(row).getByText("Spring push").closest("button")!);
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
    const confirm = within(await screen.findByRole("dialog"));
    assert.equal(confirm.queryByText("Confirm delete") !== null, true);
    fireEvent.click(confirm.getByRole("button", { name: "Delete" }));

    await waitFor(() => assert.equal(within(screen.getByRole("dialog")).queryByText("Stop the campaign before deleting it") !== null, true), { timeout: SLOW });
    const dialog = within(screen.getByRole("dialog"));
    assert.equal(dialog.getByText("Stop the campaign before deleting it").getAttribute("role"), "alert");
    assert.equal(dialog.queryByText("Confirm delete") !== null, true, "the dialog stays open on a refusal");
    assert.equal(remove.mock.callCount(), 1);
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 50)); });
    assert.equal(unhandled.length, 0, "no unhandled rejection");

    // Cancel closes the dialog and clears the refusal.
    fireEvent.click(dialog.getByRole("button", { name: "Cancel" }));
    await waitFor(() => assert.equal(screen.queryByRole("dialog") === null, true), { timeout: SLOW });
});

// MERGE2 Task 2: the campaign table uses the shared ErrorState, with their
// sentence, the request reference and the last loaded list kept on screen.
test("a failed refresh keeps the last loaded campaigns under the shared error state with the request reference", async () => {
    let fail = false;
    mock.method(dashboardApi, "listCampaigns", async () => {
        if (fail) throw new ApiClientError({ status: 500, code: "internal_error", message: "boom", requestId: "req-campaigns-1", url: "http://example.test/campaigns", method: "GET" });
        return { campaigns: [campaign] };
    });
    mountPage();
    await waitFor(() => assert.equal(screen.queryAllByRole("checkbox", { name: "Select Spring push" }).length, 1), { timeout: SLOW });

    fail = true;
    await act(async () => { await clients.at(-1)!.invalidateQueries(); });
    await waitFor(() => assert.equal(shown("Could not load campaigns."), true), { timeout: SLOW });
    assert.equal(shown("Something went wrong"), true, "the shared ErrorState");
    assert.equal(shown("Request reference: req-campaigns-1"), true);
    assert.equal(shown("boom"), false, "the server's raw message is not the sentence shown");
    assert.equal(screen.queryByRole("button", { name: "Try again" }), null);
    assert.equal(screen.queryAllByRole("checkbox", { name: "Select Spring push" }).length, 1, "the last loaded list stays on screen");

    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => assert.equal(shown("Could not load campaigns."), false), { timeout: SLOW });
    assert.equal(screen.queryAllByRole("checkbox", { name: "Select Spring push" }).length, 1);
});
