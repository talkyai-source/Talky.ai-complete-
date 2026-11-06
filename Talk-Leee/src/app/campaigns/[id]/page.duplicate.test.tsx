import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api } from "@/lib/api";
import { dashboardApi, type Campaign, type CampaignCreate } from "@/lib/dashboard-api";
import { ApiClientError } from "@/lib/http-client";
import { leadDetailsApi, type CampaignLeadField } from "@/lib/lead-details-api";

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
    if (id === "next/navigation") return { useParams: () => ({ id: route.id }), useRouter: () => router };
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
const SUCCESS_TEXT = "Duplicated as “Spring push - Copy”. These settings use their defaults on the copy: pacing, caller line, concurrent calls and retries.";
const LEAD_FIELDS_TEXT = "Duplicated as “Spring push - Copy”, but its lead fields could not be copied. Set them on the copy's edit page.";
const KNOWLEDGE_TEXT = "Duplicate isn't available yet for campaigns with knowledge files.";

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
const leadFields: CampaignLeadField[] = [
    { field_key: "email", label: "Email", field_type: "email", is_required: true, agent_visible: true, user_visible: true, options: null, sort_order: 0 },
];

type Wiring = {
    sources?: unknown[];
    knowledge?: () => Promise<unknown>;
    create?: (body: CampaignCreate) => Promise<{ campaign: Campaign }>;
    fieldWriteFails?: boolean;
};

function wire(options: Wiring = {}) {
    const calls = { bodies: [] as CampaignCreate[], fieldWrites: [] as string[] };
    mock.method(api, "getCampaignKnowledge", options.knowledge ?? (async (id: string) => (
        { campaign_id: id, knowledge_mode: "none", sources: id === "camp-1" ? (options.sources ?? []) : [], tree: [] }
    )));
    mock.method(dashboardApi, "getCampaign", async (id: string) => ({ campaign: id === "camp-2" ? copy : source }));
    mock.method(dashboardApi, "getCampaignStats", async (id: string) => (
        { campaign_id: id, campaign_status: "paused", total_leads: id === "camp-2" ? 0 : 250, goals_achieved: 0, job_status_counts: {}, call_outcome_counts: {} }
    ));
    mock.method(dashboardApi, "getMinutesStatus", async () => { throw new Error("quota"); });
    mock.method(dashboardApi, "listContacts", async () => ({ items: [], total: 0 }));
    mock.method(dashboardApi, "createCampaign", async (body: CampaignCreate) => {
        calls.bodies.push(body);
        return options.create ? options.create(body) : { campaign: copy };
    });
    mock.method(leadDetailsApi, "campaignFields", async () => ({ fields: leadFields }));
    mock.method(leadDetailsApi, "setCampaignFields", async (id: string, fields: CampaignLeadField[]) => {
        calls.fieldWrites.push(id);
        if (options.fieldWriteFails) throw new Error("lead-fields 403");
        return { fields };
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

function reasonText(): string | null {
    return screen.queryByTestId("campaign-duplicate-reason")?.textContent ?? null;
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
    assert.equal(calls.bodies.length, 1);
    assert.equal(calls.bodies[0]!.name, "Spring push - Copy");
    assert.deepEqual(calls.fieldWrites, ["camp-2"]);
});

test("the copy is made only by the shared function: the page sends no create of its own", async () => {
    const calls = wire();
    sharedOverride = async () => ({ campaign: copy, leadFieldsCopied: true, message: SUCCESS_TEXT });
    await mountReady();
    fireEvent.click(duplicateButton());
    await waitFor(() => assert.deepEqual(pushes, ["/campaigns/camp-2"]), { timeout: SLOW });
    assert.deepEqual(sharedCalls, ["camp-1"]);
    assert.equal(calls.bodies.length, 0);
    assert.deepEqual(calls.fieldWrites, []);
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
    const totalLeads = screen.getByText("Total Leads").parentElement!.textContent ?? "";
    assert.equal(totalLeads.replace("Total Leads", ""), "0", "the Total Leads card shows the copy's own count");
});

test("a failed lead-fields copy still opens the copy, showing the exact lead-fields message", async () => {
    wire({ fieldWriteFails: true });
    const view = await mountReady();
    fireEvent.click(duplicateButton());
    await waitFor(() => assert.deepEqual(pushes, ["/campaigns/camp-2"]), { timeout: SLOW });
    route.id = "camp-2";
    view.rerender(<Page />);
    await waitFor(() => assert.equal(screen.queryByRole("heading", { name: "Spring push - Copy" }) !== null, true), { timeout: SLOW });
    assert.equal(noticeText(), LEAD_FIELDS_TEXT);
});

test("a refused create shows the server's message on the source page and opens nothing", async () => {
    const serverMessage = "Voice 'voice-9' is not available for TTS provider 'cartesia'. Pick a matching voice or change the provider.";
    const calls = wire({ create: async () => { throw new Error(serverMessage); } });
    await mountReady();
    fireEvent.click(duplicateButton());
    await waitFor(() => assert.equal(noticeText(), serverMessage), { timeout: SLOW });
    assert.equal(screen.getByTestId("campaign-duplicate-notice").getAttribute("role"), "alert");
    assert.deepEqual(pushes, []);
    assert.deepEqual(calls.fieldWrites, []);
    assert.equal(screen.queryByRole("heading", { name: "Spring push" }) !== null, true);
});

test("detail-page Duplicate is disabled with the exact sentence when the campaign has a knowledge source", async () => {
    const calls = wire({ sources: [{ id: "k1", filename: "faq.pdf", token_count: 10, version: 1, status: "ready", created_at: "2026-01-01T00:00:00Z" }] });
    render(<Page />);
    await waitFor(() => assert.equal(reasonText(), KNOWLEDGE_TEXT), { timeout: SLOW });
    assert.equal(duplicateButton().disabled, true);
    fireEvent.click(duplicateButton());
    assert.deepEqual(sharedCalls, []);
    assert.equal(calls.bodies.length, 0);
});

test("detail-page Duplicate is disabled with the same sentence when the knowledge read fails", async () => {
    const calls = wire({ knowledge: async () => { throw new Error("knowledge 503"); } });
    render(<Page />);
    await waitFor(() => assert.equal(reasonText(), KNOWLEDGE_TEXT), { timeout: SLOW });
    assert.equal(duplicateButton().disabled, true);
    fireEvent.click(duplicateButton());
    assert.deepEqual(sharedCalls, []);
    assert.equal(calls.bodies.length, 0);
});

test("knowledge added after the page loaded is caught at the click: nothing is created and Duplicate turns off", async () => {
    let hasKnowledge = false;
    const calls = wire({
        knowledge: async () => ({
            campaign_id: "camp-1", knowledge_mode: "none", tree: [],
            sources: hasKnowledge ? [{ id: "k1", filename: "faq.pdf", token_count: 10, version: 1, status: "ready", created_at: "2026-01-01T00:00:00Z" }] : [],
        }),
    });
    await mountReady();
    hasKnowledge = true;
    fireEvent.click(duplicateButton());
    await waitFor(() => assert.equal(reasonText(), KNOWLEDGE_TEXT), { timeout: SLOW });
    assert.equal(duplicateButton().disabled, true);
    assert.equal(calls.bodies.length, 0);
    assert.deepEqual(pushes, []);
});

test("the detail-page Duplicate button is disabled while its request runs", async () => {
    let finish!: (value: { campaign: Campaign }) => void;
    const calls = wire({ create: () => new Promise((resolve) => { finish = resolve; }) });
    await mountReady();
    fireEvent.click(duplicateButton());
    await waitFor(() => assert.equal(calls.bodies.length, 1), { timeout: SLOW });
    assert.equal(duplicateButton().disabled, true, "disabled while the POST is pending");
    fireEvent.click(duplicateButton());
    assert.equal(calls.bodies.length, 1, "a second click sends nothing");
    assert.deepEqual(sharedCalls, ["camp-1"]);
    await act(async () => { finish({ campaign: copy }); });
    await waitFor(() => assert.deepEqual(pushes, ["/campaigns/camp-2"]), { timeout: SLOW });
});

test("detail-page Duplicate is enabled when the knowledge feature is off, and the copy is made", async () => {
    const calls = wire({ knowledge: async () => { throw new ApiClientError({ status: 404, code: "not_found", message: "Campaign knowledge is not enabled", url: "http://example.test/campaigns/x/knowledge", method: "GET" }); } });
    await mountReady();
    assert.equal(reasonText(), null);
    fireEvent.click(duplicateButton());
    await waitFor(() => assert.deepEqual(pushes, ["/campaigns/camp-2"]), { timeout: SLOW });
    assert.deepEqual(sharedCalls, ["camp-1"]);
    assert.equal(calls.bodies.length, 1);
});

test("a detail-page knowledge read that fails with any other 404 still disables Duplicate with the sentence", async () => {
    const calls = wire({ knowledge: async () => { throw new ApiClientError({ status: 404, code: "campaign_not_found", message: "Campaign not found", url: "http://example.test/campaigns/x/knowledge", method: "GET" }); } });
    render(<Page />);
    await waitFor(() => assert.equal(reasonText(), KNOWLEDGE_TEXT), { timeout: SLOW });
    assert.equal(duplicateButton().disabled, true);
    fireEvent.click(duplicateButton());
    assert.deepEqual(sharedCalls, []);
    assert.equal(calls.bodies.length, 0);
});
