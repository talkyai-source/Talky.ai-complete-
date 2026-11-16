import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api } from "@/lib/api";
import { dashboardApi, type Campaign } from "@/lib/dashboard-api";
import { ApiClientError } from "@/lib/http-client";

// Real page, real Duplicate hook and real shared copy routine. The one seam is
// around lib/campaign-duplicate: every export is the real one, and
// `duplicateCampaign` is routed through `sharedCalls` so a test can prove the
// detail page copies through that shared function (and can stand in for it).
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
const route = { id: "camp-1" };
const pushes: string[] = [];
// Stable identity like the real router: loadData depends on it.
const router = { push(href: string) { pushes.push(href); }, replace() {} };
type Shared = typeof import("@/lib/campaign-duplicate");
const sharedCalls: string[] = [];
let sharedOverride: Shared["duplicateCampaign"] | null = null;
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { useParams: () => ({ id: route.id }), useRouter: () => router, useSearchParams: () => new URLSearchParams() };
    if (id.includes("lib/campaign-duplicate")) {
        const real = originalLoad.call(this, id, ...args) as Shared;
        return {
            ...real,
            duplicateCampaign: (sourceId: string) => {
                sharedCalls.push(sourceId);
                return (sharedOverride ?? real.duplicateCampaign)(sourceId);
            },
        };
    }
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/campaigns/smart-csv-import")) return { SmartCsvImport: nothing };
    if (id.includes("components/campaigns/contact-lists")) return { ContactLists: nothing, ActiveContactsSummary: nothing };
    if (id.includes("components/campaigns/script-card")) return { ScriptCard: nothing };
    if (id.includes("components/campaigns/live-calls-panel")) return { LiveCallsPanel: nothing };
    if (id.includes("components/campaigns/call-issues-panel")) return { CallIssuesPanel: nothing };
    if (id.includes("components/campaigns/knowledge-panel")) return { KnowledgePanel: nothing };
    if (id.includes("components/campaigns/test-agent-button")) return { TestAgentButton: nothing };
    if (id.includes("components/campaigns/campaign-readiness")) return {
        CampaignReadinessNotice: nothing,
        useCampaignReadiness: () => ({ ready: true, checking: false, reason: "", error: false, retry: async () => [] }),
    };
    if (id.includes("components/calls/lead-details-panel")) return { ContactCapturedDetails: nothing };
    if (id.includes("lib/queries/inbound-queries")) return { useEffectivePermissions: () => ({ isSuccess: false, data: undefined }) };
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

afterEach(() => {
    cleanup();
    mock.restoreAll();
    route.id = "camp-1";
    pushes.length = 0;
    sharedCalls.length = 0;
    sharedOverride = null;
});

const source = {
    id: "camp-1", name: "Spring push", status: "paused", direction: "outbound",
    system_prompt: "", voice_id: "voice-9", max_concurrent_calls: 7,
    total_leads: 250, calls_completed: 120, calls_failed: 9, created_at: "2026-01-01T00:00:00Z",
    script_config: { persona_type: "lead_gen", company_name: "Acme", agent_names: ["Ava"], campaign_slots: {} },
} as unknown as Campaign;
const copy = { ...source, id: "camp-2", name: "Spring push - Copy", status: "draft", total_leads: 0, calls_completed: 0, calls_failed: 0 } as Campaign;
type Wiring = {
    sources?: unknown[];
    /** The backend's answer to POST /campaigns/{id}/duplicate. */
    duplicate?: (id: string) => Promise<{ campaign: Campaign }>;
};

function wire(options: Wiring = {}) {
    const calls = { duplicates: [] as string[], knowledgeReads: 0 };
    // Duplicate must not read knowledge in the browser any more: the backend copies it.
    mock.method(api, "getCampaignKnowledge", async (id: string) => {
        calls.knowledgeReads++;
        return { campaign_id: id, knowledge_mode: "none", sources: id === "camp-1" ? (options.sources ?? []) : [], tree: [] };
    });
    mock.method(dashboardApi, "getCampaign", async (id: string) => ({ campaign: id === "camp-2" ? copy : source }));
    mock.method(dashboardApi, "getCampaignStats", async (id: string) => (
        { campaign_id: id, campaign_status: "paused", total_leads: id === "camp-2" ? 0 : 250, goals_achieved: 0, job_status_counts: {}, call_outcome_counts: {} }
    ));
    mock.method(dashboardApi, "getMinutesStatus", async () => { throw new Error("quota"); });
    mock.method(dashboardApi, "listContacts", async () => ({ items: [], total: 0 }));
    mock.method(dashboardApi, "duplicateCampaign", async (id: string) => {
        calls.duplicates.push(id);
        return options.duplicate ? options.duplicate(id) : { campaign: copy };
    });
    return calls;
}

function duplicateButton() {
    return screen.getByRole("button", { name: "Duplicate" }) as HTMLButtonElement;
}

async function mountReady() {
    const view = render(<Page />);
    await waitFor(() => assert.equal(duplicateButton().disabled, false), { timeout: SLOW });
    return view;
}

function noticeText(): string | null {
    return screen.queryByTestId("campaign-duplicate-notice")?.textContent ?? null;
}

test("the detail page of an outbound campaign offers Duplicate next to Edit", async () => {
    wire();
    await mountReady();
    const actions = duplicateButton().parentElement!;
    const labels = Array.from(actions.querySelectorAll("button")).map((button) => button.textContent?.trim());
    assert.deepEqual(labels, ["Edit", "Duplicate", "Resume"]);
});

test("detail-page Duplicate uses the shared duplicateCampaign and then opens the new campaign", async () => {
    const calls = wire();
    await mountReady();
    fireEvent.click(duplicateButton());
    await waitFor(() => assert.deepEqual(pushes, ["/campaigns/camp-2"]), { timeout: SLOW });
    assert.deepEqual(sharedCalls, ["camp-1"], "copied through lib/campaign-duplicate's duplicateCampaign, once");
    assert.deepEqual(calls.duplicates, ["camp-1"], "one POST /campaigns/camp-1/duplicate");
});

test("the copy is made only by the shared function: the page sends no duplicate request of its own", async () => {
    const calls = wire();
    sharedOverride = async () => ({ campaign: copy, message: SUCCESS_TEXT });
    await mountReady();
    fireEvent.click(duplicateButton());
    await waitFor(() => assert.deepEqual(pushes, ["/campaigns/camp-2"]), { timeout: SLOW });
    assert.deepEqual(sharedCalls, ["camp-1"]);
    assert.deepEqual(calls.duplicates, []);
});

test("after success the copy's page shows the exact success message and the copy's own 0 leads", async () => {
    wire();
    const view = await mountReady();
    fireEvent.click(duplicateButton());
    await waitFor(() => assert.deepEqual(pushes, ["/campaigns/camp-2"]), { timeout: SLOW });
    route.id = "camp-2";
    view.rerender(<Page />);
    await waitFor(() => assert.equal(screen.queryByRole("heading", { name: "Spring push - Copy" }) !== null, true), { timeout: SLOW });
    assert.equal(noticeText(), SUCCESS_TEXT);
    assert.equal(screen.getByTestId("campaign-duplicate-notice").getAttribute("role"), "status");
    const totalLeads = screen.getAllByText("Contacts").find((el) => el.tagName === "P")!.parentElement!.textContent ?? "";
    assert.equal(totalLeads.replace("Contacts", ""), "0", "the Contacts card shows the copy's own count");
});

test("a refused create shows the server's message on the source page and opens nothing", async () => {
    const serverMessage = "Campaign knowledge must be ready before duplicating.";
    const calls = wire({ duplicate: async () => { throw new ApiClientError({ status: 409, code: "conflict", message: serverMessage, url: "http://example.test/campaigns/camp-1/duplicate", method: "POST" }); } });
    await mountReady();
    fireEvent.click(duplicateButton());
    await waitFor(() => assert.equal(noticeText(), serverMessage), { timeout: SLOW });
    assert.equal(screen.getByTestId("campaign-duplicate-notice").getAttribute("role"), "alert");
    assert.deepEqual(pushes, []);
    assert.deepEqual(calls.duplicates, ["camp-1"]);
    assert.equal(screen.queryByRole("heading", { name: "Spring push" }) !== null, true);
});

test("the detail-page Duplicate button is disabled while its request runs", async () => {
    let finish!: (value: { campaign: Campaign }) => void;
    const calls = wire({ duplicate: () => new Promise((resolve) => { finish = resolve; }) });
    await mountReady();
    fireEvent.click(duplicateButton());
    await waitFor(() => assert.equal(calls.duplicates.length, 1), { timeout: SLOW });
    assert.equal(duplicateButton().disabled, true, "disabled while the POST is pending");
    fireEvent.click(duplicateButton());
    assert.equal(calls.duplicates.length, 1, "a second click sends nothing");
    assert.deepEqual(sharedCalls, ["camp-1"]);
    await act(async () => { finish({ campaign: copy }); });
    await waitFor(() => assert.deepEqual(pushes, ["/campaigns/camp-2"]), { timeout: SLOW });
});

test("a campaign with knowledge files is duplicated from the detail page: it is not refused and the browser reads no knowledge", async () => {
    const calls = wire({ sources: [{ id: "k1", filename: "faq.pdf", token_count: 10, version: 1, status: "ready", created_at: "2026-01-01T00:00:00Z" }] });
    await mountReady();
    assert.equal(screen.queryByText(/Duplicate isn't available/) === null, true);
    fireEvent.click(duplicateButton());
    await waitFor(() => assert.deepEqual(pushes, ["/campaigns/camp-2"]), { timeout: SLOW });
    assert.deepEqual(sharedCalls, ["camp-1"]);
    assert.deepEqual(calls.duplicates, ["camp-1"]);
    assert.equal(calls.knowledgeReads, 0);
});
