import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { api } from "@/lib/api";
import { queryKeys } from "@/lib/api-hooks";
import { dashboardApi, type Campaign, type CampaignCreate } from "@/lib/dashboard-api";
import { ApiClientError } from "@/lib/http-client";
import { leadDetailsApi, type CampaignLeadField } from "@/lib/lead-details-api";

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
const SUCCESS_TEXT = "Duplicated as “Spring push - Copy”. These settings use their defaults on the copy: pacing, caller line, concurrent calls and retries.";
const LEAD_FIELDS_TEXT = "Duplicated as “Spring push - Copy”, but its lead fields could not be copied. Set them on the copy's edit page.";
const KNOWLEDGE_TEXT = "Duplicate isn't available yet for campaigns with knowledge files.";

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
const leadFields: CampaignLeadField[] = [
    { field_key: "email", label: "Email", field_type: "email", is_required: true, agent_visible: true, user_visible: true, options: null, sort_order: 0 },
];

type Wiring = {
    sources?: unknown[];
    knowledge?: () => Promise<unknown>;
    create?: (body: CampaignCreate) => Promise<{ campaign: Campaign }>;
    fieldWriteFails?: boolean;
    /** What GET /campaigns returns once the copy has been created. */
    listAfterCreate?: Campaign[];
};

function wire(options: Wiring = {}) {
    const calls = { lists: 0, bodies: [] as CampaignCreate[], fieldWrites: [] as string[] };
    let createdOnBackend = false;
    mock.method(api, "request", async () => ({ ready: true }));
    mock.method(api, "getCampaignKnowledge", options.knowledge ?? (async (id: string) => (
        { campaign_id: id, knowledge_mode: "none", sources: options.sources ?? [], tree: [] }
    )));
    mock.method(dashboardApi, "listCampaigns", async () => {
        calls.lists++;
        return { campaigns: createdOnBackend ? (options.listAfterCreate ?? [copy, source]) : [source] };
    });
    mock.method(dashboardApi, "getCampaign", async () => ({ campaign: source }));
    mock.method(dashboardApi, "createCampaign", async (body: CampaignCreate) => {
        calls.bodies.push(body);
        const result = await (options.create ? options.create(body) : Promise.resolve({ campaign: copy }));
        createdOnBackend = true;
        return result;
    });
    mock.method(leadDetailsApi, "campaignFields", async () => ({ fields: leadFields }));
    mock.method(leadDetailsApi, "setCampaignFields", async (id: string, fields: CampaignLeadField[]) => {
        calls.fieldWrites.push(id);
        if (options.fieldWriteFails) throw new Error("lead-fields 403");
        return { fields };
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

function reasonText(dialog: ReturnType<typeof within>): string | null {
    return dialog.queryByTestId("campaign-duplicate-reason")?.textContent ?? null;
}

test("Duplicate creates a real backend campaign: the list is refetched and the copy appears from the backend response", async () => {
    const calls = wire();
    await mountPage();
    assert.equal(calls.lists, 1);
    await openAndDuplicate();
    await waitFor(() => assert.equal(rowsNamed("Spring push - Copy").length, 1), { timeout: SLOW });
    assert.equal(calls.bodies.length, 1, "one real POST /campaigns");
    assert.equal(calls.bodies[0]!.name, "Spring push - Copy");
    assert.deepEqual(calls.fieldWrites, ["new-1"], "lead fields were written to the new campaign");
    assert.equal(calls.lists, 2, "GET /campaigns was read again after the create");
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

test("Duplicate stays disabled, without a reason, until the knowledge read answers", async () => {
    let answer!: (value: unknown) => void;
    const calls = wire({ knowledge: () => new Promise((resolve) => { answer = resolve; }) });
    await mountPage();
    const dialog = openDetails("Spring push");
    assert.equal(duplicateButton(dialog).disabled, true);
    assert.equal(reasonText(dialog), null);
    fireEvent.click(duplicateButton(dialog));
    assert.equal(calls.bodies.length, 0);
    await act(async () => { answer({ campaign_id: "src-1", knowledge_mode: "none", sources: [], tree: [] }); });
    await waitFor(() => assert.equal(duplicateButton(dialog).disabled, false), { timeout: SLOW });
    assert.equal(reasonText(dialog), null);
});

test("Duplicate is disabled with the exact sentence when the campaign has a knowledge source", async () => {
    const calls = wire({ sources: [{ id: "k1", filename: "faq.pdf", token_count: 10, version: 1, status: "ready", created_at: "2026-01-01T00:00:00Z" }] });
    await mountPage();
    const dialog = openDetails("Spring push");
    await waitFor(() => assert.equal(reasonText(dialog), KNOWLEDGE_TEXT), { timeout: SLOW });
    assert.equal(duplicateButton(dialog).disabled, true);
    fireEvent.click(duplicateButton(dialog));
    assert.equal(calls.bodies.length, 0);
});

test("Duplicate is disabled with the same sentence when the knowledge read fails", async () => {
    const calls = wire({ knowledge: async () => { throw new Error("knowledge 503"); } });
    await mountPage();
    const dialog = openDetails("Spring push");
    await waitFor(() => assert.equal(reasonText(dialog), KNOWLEDGE_TEXT), { timeout: SLOW });
    assert.equal(duplicateButton(dialog).disabled, true);
    fireEvent.click(duplicateButton(dialog));
    assert.equal(calls.bodies.length, 0);
});

test("the Duplicate button is disabled while its request runs", async () => {
    let finish!: (value: { campaign: Campaign }) => void;
    const calls = wire({ create: () => new Promise((resolve) => { finish = resolve; }) });
    await mountPage();
    const dialog = await openAndDuplicate();
    await waitFor(() => assert.equal(calls.bodies.length, 1), { timeout: SLOW });
    assert.equal(duplicateButton(dialog).disabled, true, "disabled while the POST is pending");
    fireEvent.click(duplicateButton(dialog));
    assert.equal(calls.bodies.length, 1, "a second click sends nothing");
    await act(async () => { finish({ campaign: copy }); });
    await waitFor(() => assert.equal(duplicateButton(dialog).disabled, false), { timeout: SLOW });
    assert.equal(calls.bodies.length, 1);
});

test("full success shows the exact success message in the details modal", async () => {
    wire();
    await mountPage();
    const dialog = await openAndDuplicate();
    await waitFor(() => assert.equal(noticeText(dialog), SUCCESS_TEXT), { timeout: SLOW });
    assert.equal(dialog.getByTestId("campaign-duplicate-notice").getAttribute("role"), "status");
});

test("a failed lead-fields copy shows the exact lead-fields message and the copy still appears", async () => {
    wire({ fieldWriteFails: true });
    await mountPage();
    const dialog = await openAndDuplicate();
    await waitFor(() => assert.equal(noticeText(dialog), LEAD_FIELDS_TEXT), { timeout: SLOW });
    assert.equal(rowsNamed("Spring push - Copy").length, 1);
});

test("a refused create shows the server's message and creates nothing in the UI", async () => {
    const serverMessage = "Voice 'voice-9' is not available for TTS provider 'cartesia'. Pick a matching voice or change the provider.";
    const calls = wire({ create: async () => { throw new Error(serverMessage); } });
    const client = await mountPage();
    const dialog = await openAndDuplicate();
    await waitFor(() => assert.equal(noticeText(dialog), serverMessage), { timeout: SLOW });
    assert.equal(dialog.getByTestId("campaign-duplicate-notice").getAttribute("role"), "alert");
    assert.equal(rowsNamed("Spring push - Copy").length, 0);
    assert.deepEqual((client.getQueryData<Campaign[]>(queryKeys.campaigns()) ?? []).map((c) => c.id), ["src-1"]);
    assert.deepEqual(calls.fieldWrites, []);
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

test("Duplicate is enabled when the knowledge feature is off, and the copy is made", async () => {
    const calls = wire({ knowledge: async () => { throw new ApiClientError({ status: 404, code: "not_found", message: "Campaign knowledge is not enabled", url: "http://example.test/campaigns/x/knowledge", method: "GET" }); } });
    await mountPage();
    const dialog = await openAndDuplicate();
    await waitFor(() => assert.equal(noticeText(dialog), SUCCESS_TEXT), { timeout: SLOW });
    assert.equal(reasonText(dialog), null);
    assert.equal(calls.bodies.length, 1);
    assert.equal(rowsNamed("Spring push - Copy").length, 1);
});

test("a knowledge read that fails with any other 404 still disables Duplicate with the sentence", async () => {
    const calls = wire({ knowledge: async () => { throw new ApiClientError({ status: 404, code: "campaign_not_found", message: "Campaign not found", url: "http://example.test/campaigns/x/knowledge", method: "GET" }); } });
    await mountPage();
    const dialog = openDetails("Spring push");
    await waitFor(() => assert.equal(reasonText(dialog), KNOWLEDGE_TEXT), { timeout: SLOW });
    assert.equal(duplicateButton(dialog).disabled, true);
    fireEvent.click(duplicateButton(dialog));
    assert.equal(calls.bodies.length, 0);
});
