import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { api } from "@/lib/api";
import { queryKeys } from "@/lib/api-hooks";
import { dashboardApi, type Campaign } from "@/lib/dashboard-api";
import { ApiClientError } from "@/lib/http-client";

// Real page, real table, real shared duplicate routine and real API objects
// (their methods are mocked per test). Only the layout and the three sibling
// panels, which fetch on their own, are stubbed.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/campaigns/event-stream")) return { EventStream: nothing };
    if (id.includes("components/campaigns/alert-timeline")) return { AlertTimeline: nothing };
    if (id.includes("components/campaigns/command-bar")) return { CommandBar: nothing };
    return originalLoad.call(this, id, ...args);
};
let Page: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Page = require("./page").default;
} finally {
    loader._load = originalLoad;
}

// Generous: the full suite runs many files in parallel on a loaded machine.
const SLOW = 15_000;
const SUCCESS_TEXT = "Duplicated as “Spring push - Copy”. The copy is a draft with the same settings, knowledge files and lead fields. Contacts, calls and results are not copied.";

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

const source = {
    id: "src-1", name: "Spring push", description: "Q2 outreach", status: "running", direction: "outbound",
    system_prompt: "", voice_id: "voice-9", max_concurrent_calls: 7,
    total_leads: 250, calls_completed: 120, calls_failed: 9, created_at: "2026-01-01T00:00:00Z",
    script_config: { persona_type: "lead_gen", company_name: "Acme", agent_names: ["Ava"], campaign_slots: {} },
} as unknown as Campaign;
// What the backend reports for the new row: its own values, nothing carried over.
const copy = { ...source, id: "new-1", name: "Spring push - Copy", status: "draft", total_leads: 0, calls_completed: 0, calls_failed: 0, created_at: "2026-02-01T00:00:00Z" } as Campaign;
type Wiring = {
    sources?: unknown[];
    /** The backend's answer to POST /campaigns/{id}/duplicate. */
    duplicate?: (id: string) => Promise<{ campaign: Campaign }>;
    /** What GET /campaigns returns once the copy has been created. */
    listAfterCreate?: Campaign[];
};

function wire(options: Wiring = {}) {
    const calls = { lists: 0, duplicates: [] as string[], knowledgeReads: 0 };
    let createdOnBackend = false;
    mock.method(api, "request", async () => ({ ready: true }));
    // Duplicate must not read knowledge in the browser any more: the backend copies it.
    mock.method(api, "getCampaignKnowledge", async (id: string) => {
        calls.knowledgeReads++;
        return { campaign_id: id, knowledge_mode: "none", sources: options.sources ?? [], tree: [] };
    });
    mock.method(dashboardApi, "listCampaigns", async () => {
        calls.lists++;
        return { campaigns: createdOnBackend ? (options.listAfterCreate ?? [copy, source]) : [source] };
    });
    mock.method(dashboardApi, "getCampaign", async () => ({ campaign: source }));
    mock.method(dashboardApi, "duplicateCampaign", async (id: string) => {
        calls.duplicates.push(id);
        const result = await (options.duplicate ? options.duplicate(id) : Promise.resolve({ campaign: copy }));
        createdOnBackend = true;
        return result;
    });
    return calls;
}

async function mountPage() {
    window.requestAnimationFrame = (callback) => window.setTimeout(() => callback(performance.now()), 0);
    window.cancelAnimationFrame = (id) => window.clearTimeout(id);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.push(client);
    render(<AppRouterContext.Provider value={router}><QueryClientProvider client={client}><Page /></QueryClientProvider></AppRouterContext.Provider>);
    await waitFor(() => assert.equal(rowsNamed("Spring push").length, 1), { timeout: SLOW });
    // Finish the table's initial preference hydration and selection-reset frames.
    await act(async () => { await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve())); });
    await act(async () => { await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve())); });
    return client;
}

function rowsNamed(name: string): HTMLElement[] {
    return screen.queryAllByRole("checkbox", { name: `Select ${name}` })
        .map((box) => box.closest("[role='row']") as HTMLElement);
}

function openDetails(name: string) {
    const row = rowsNamed(name)[0]!;
    fireEvent.click(within(row).getByText(name).closest("button")!);
    return within(screen.getByRole("dialog"));
}

function duplicateButton(dialog: ReturnType<typeof within>) {
    return dialog.getByRole("button", { name: "Duplicate" }) as HTMLButtonElement;
}

async function openAndDuplicate() {
    const dialog = openDetails("Spring push");
    await waitFor(() => assert.equal(duplicateButton(dialog).disabled, false), { timeout: SLOW });
    fireEvent.click(duplicateButton(dialog));
    return dialog;
}

function noticeText(dialog: ReturnType<typeof within>): string | null {
    return dialog.queryByTestId("campaign-duplicate-notice")?.textContent ?? null;
}

test("Duplicate creates a real backend campaign: the list is refetched and the copy appears from the backend response", async () => {
    const calls = wire();
    await mountPage();
    assert.equal(calls.lists, 1);
    await openAndDuplicate();
    await waitFor(() => assert.equal(rowsNamed("Spring push - Copy").length, 1), { timeout: SLOW });
    assert.deepEqual(calls.duplicates, ["src-1"], "one POST /campaigns/src-1/duplicate");
    assert.equal(calls.lists, 2, "GET /campaigns was read again after the copy");
});

test("no cache-only row is added: the list shows only what the backend returns", async () => {
    // The refetched list does not include the copy, so the only way a copy
    // row could appear is a row written straight into the cache.
    const calls = wire({ listAfterCreate: [source] });
    const client = await mountPage();
    const dialog = await openAndDuplicate();
    await waitFor(() => assert.equal(noticeText(dialog), SUCCESS_TEXT), { timeout: SLOW });
    assert.equal(calls.lists, 2);
    assert.equal(rowsNamed("Spring push - Copy").length, 0);
    assert.equal(screen.getAllByRole("checkbox", { name: /^Select Spring push/ }).length, 1);
    const cached = client.getQueryData<Campaign[]>(queryKeys.campaigns()) ?? [];
    assert.deepEqual(cached.map((c) => c.id), ["src-1"]);
});

test("the copy shows 0 leads and no calls, never the source's counters", async () => {
    wire();
    await mountPage();
    await openAndDuplicate();
    await waitFor(() => assert.equal(rowsNamed("Spring push - Copy").length, 1), { timeout: SLOW });
    const cells = (name: string) => Array.from(rowsNamed(name)[0]!.children).slice(5).map((cell) => cell.textContent);
    assert.deepEqual(cells("Spring push"), ["250", "120", "9"], "the source keeps its own leads / completed / failed");
    assert.deepEqual(cells("Spring push - Copy"), ["0", "0", "0"], "the copy shows the backend's leads / completed / failed");
});

test("the Duplicate button is disabled while its request runs", async () => {
    let finish!: (value: { campaign: Campaign }) => void;
    const calls = wire({ duplicate: () => new Promise((resolve) => { finish = resolve; }) });
    await mountPage();
    const dialog = await openAndDuplicate();
    await waitFor(() => assert.equal(calls.duplicates.length, 1), { timeout: SLOW });
    assert.equal(duplicateButton(dialog).disabled, true, "disabled while the POST is pending");
    fireEvent.click(duplicateButton(dialog));
    assert.equal(calls.duplicates.length, 1, "a second click sends nothing");
    await act(async () => { finish({ campaign: copy }); });
    await waitFor(() => assert.equal(duplicateButton(dialog).disabled, false), { timeout: SLOW });
    assert.equal(calls.duplicates.length, 1);
});

test("full success shows the exact success message in the details modal", async () => {
    wire();
    await mountPage();
    const dialog = await openAndDuplicate();
    await waitFor(() => assert.equal(noticeText(dialog), SUCCESS_TEXT), { timeout: SLOW });
    assert.equal(dialog.getByTestId("campaign-duplicate-notice").getAttribute("role"), "status");
});

test("a refused create shows the server's message and creates nothing in the UI", async () => {
    const serverMessage = "Campaign knowledge must be ready before duplicating.";
    const calls = wire({ duplicate: async () => { throw new ApiClientError({ status: 409, code: "conflict", message: serverMessage, url: "http://example.test/campaigns/src-1/duplicate", method: "POST" }); } });
    const client = await mountPage();
    const dialog = await openAndDuplicate();
    await waitFor(() => assert.equal(noticeText(dialog), serverMessage), { timeout: SLOW });
    assert.equal(dialog.getByTestId("campaign-duplicate-notice").getAttribute("role"), "alert");
    assert.equal(rowsNamed("Spring push - Copy").length, 0);
    assert.deepEqual((client.getQueryData<Campaign[]>(queryKeys.campaigns()) ?? []).map((c) => c.id), ["src-1"]);
    assert.deepEqual(calls.duplicates, ["src-1"]);
    assert.equal(duplicateButton(dialog).disabled, false, "Duplicate can be tried again");
});

test("a message never follows the modal to another campaign", async () => {
    wire();
    await mountPage();
    const dialog = await openAndDuplicate();
    await waitFor(() => assert.equal(noticeText(dialog), SUCCESS_TEXT), { timeout: SLOW });
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => assert.equal(screen.queryByRole("dialog") === null, true), { timeout: SLOW });
    const other = openDetails("Spring push - Copy");
    assert.equal(noticeText(other), null);
});

test("a campaign with knowledge files is duplicated from the list: it is not refused and the browser reads no knowledge", async () => {
    const calls = wire({ sources: [{ id: "k1", filename: "faq.pdf", token_count: 10, version: 1, status: "ready", created_at: "2026-01-01T00:00:00Z" }] });
    await mountPage();
    const dialog = await openAndDuplicate();
    await waitFor(() => assert.equal(noticeText(dialog), SUCCESS_TEXT), { timeout: SLOW });
    assert.equal(dialog.queryByText(/Duplicate isn't available/) === null, true);
    assert.deepEqual(calls.duplicates, ["src-1"]);
    assert.equal(calls.knowledgeReads, 0);
    assert.equal(rowsNamed("Spring push - Copy").length, 1);
});

test("Duplicate becomes enabled without any knowledge read, and nothing explains a refusal that is not there", async () => {
    const calls = wire({ sources: [{ id: "k1", filename: "faq.pdf", token_count: 10, version: 1, status: "ready", created_at: "2026-01-01T00:00:00Z" }] });
    await mountPage();
    const dialog = openDetails("Spring push");
    await waitFor(() => assert.equal(duplicateButton(dialog).disabled, false), { timeout: SLOW });
    assert.equal(dialog.queryByText(/Duplicate isn't available/) === null, true);
    assert.equal(calls.knowledgeReads, 0);
});
