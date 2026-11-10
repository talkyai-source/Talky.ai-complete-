import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { aiOptionsApi } from "@/lib/ai-options-api";
import { dashboardApi, type Campaign, type CampaignCreate } from "@/lib/dashboard-api";
import { leadDetailsApi } from "@/lib/lead-details-api";

// QA F1-22 (QA-UI-042, QA-UI-044, QA-UI-066): a reload, Back then Forward, or
// a lost sign-in cleared every field of the outbound campaign wizard; only
// the inbound flow kept a draft. Outbound drafts are now kept in this tab
// under one key per account and removed once the campaign is created.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const pushes: string[] = [];
const router = { push(href: string) { pushes.push(href); }, replace() {}, back() {}, refresh() {}, prefetch: async () => {} };
type TestUser = { id: string; tenant_id?: string } | null;
let currentUser: TestUser = null;
let permissionsQuery: { isSuccess: boolean; data?: { permissions: string[] } } = { isSuccess: false };
loader._load = function (id, ...args) {
    if (id === "next/navigation") return {
        // Like Next's router, the search params follow the address (the wizard's steps live there).
        useRouter: () => router, usePathname: () => "/campaigns/new", useSearchParams: () => new URLSearchParams(window.location.search), useParams: () => ({}),
    };
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("hooks/useAuth")) return { useAuth: () => ({ user: currentUser }) };
    if (id.includes("lib/queries/inbound-queries")) return { useEffectivePermissions: () => permissionsQuery };
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
const ACCOUNT_A = { id: "user-a", tenant_id: "tenant-a" };
const ACCOUNT_B = { id: "user-b", tenant_id: "tenant-b" };
const WIZARD_A = "talky:campaign-new:tenant-a:user-a:wizard";
const REALTIME = { pipeline_mode: "realtime" as const, realtime_model: "gpt-realtime", realtime_voice: "alloy" };
const CREATED = { id: "campaign-1", name: "Spring push", status: "draft", direction: "outbound" } as unknown as Campaign;
const original = {
    createCampaign: dashboardApi.createCampaign, previewCampaignPrompt: dashboardApi.previewCampaignPrompt,
    getProviders: aiOptionsApi.getProviders, getConfig: aiOptionsApi.getConfig, getVoices: aiOptionsApi.getVoices,
    fieldSpec: leadDetailsApi.fieldSpec, campaignFields: leadDetailsApi.campaignFields, setCampaignFields: leadDetailsApi.setCampaignFields,
};
const clients: QueryClient[] = [];
let created: CampaignCreate | null = null;

beforeEach(() => {
    created = null;
    pushes.splice(0);
    window.sessionStorage.clear();
    window.history.replaceState(null, "", "/campaigns/new");
    aiOptionsApi.getProviders = async () => ({
        realtime: { available: true, model: "gpt-realtime", voices: [{ id: "alloy", name: "Alloy" }] },
    }) as unknown as Awaited<ReturnType<typeof aiOptionsApi.getProviders>>;
    aiOptionsApi.getConfig = async () => ({ ...REALTIME, tts_provider: "cartesia" }) as unknown as Awaited<ReturnType<typeof aiOptionsApi.getConfig>>;
    aiOptionsApi.getVoices = async () => ({ voices: [] });
    leadDetailsApi.fieldSpec = async () => ({ fields: [], csv_template_headers: [] });
    leadDetailsApi.campaignFields = async () => ({ fields: [] });
    leadDetailsApi.setCampaignFields = (async () => ({ fields: [] })) as unknown as typeof leadDetailsApi.setCampaignFields;
    dashboardApi.createCampaign = async (data) => { created = data; return { campaign: CREATED }; };
    dashboardApi.previewCampaignPrompt = (async () => ({
        system_prompt: "", greeting: "", prompt_chars: 0, layers: [],
    })) as unknown as typeof dashboardApi.previewCampaignPrompt;
});

afterEach(() => {
    cleanup();
    clients.splice(0).forEach((client) => client.clear());
    window.sessionStorage.clear();
    permissionsQuery = { isSuccess: false };
    Object.assign(dashboardApi, { createCampaign: original.createCampaign, previewCampaignPrompt: original.previewCampaignPrompt });
    Object.assign(aiOptionsApi, { getProviders: original.getProviders, getConfig: original.getConfig, getVoices: original.getVoices });
    Object.assign(leadDetailsApi, { fieldSpec: original.fieldSpec, campaignFields: original.campaignFields, setCampaignFields: original.setCampaignFields });
});

function tree(client: QueryClient) {
    return <QueryClientProvider client={client}><Page /></QueryClientProvider>;
}
function mountPage(user: TestUser) {
    currentUser = user;
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.push(client);
    return { client, view: render(tree(client)) };
}
/** A reload in this tab: the page goes away and comes back. */
async function reload(user: TestUser) {
    cleanup();
    mountPage(user);
    await screen.findByLabelText(/^Campaign name$/i, {}, { timeout: SLOW });
}
async function typeName(value: string) {
    const input = await screen.findByLabelText(/^Campaign name$/i, {}, { timeout: SLOW });
    fireEvent.change(input, { target: { value } });
}
const nameValue = () => (screen.getByLabelText(/^Campaign name$/i) as HTMLInputElement).value;
const keptNotice = () => screen.queryByText(/We kept the campaign you were setting up in this tab/);

test("a reload keeps the outbound wizard's draft and says so", async () => {
    mountPage(ACCOUNT_A);
    await typeName("Spring push");
    await reload(ACCOUNT_A);
    assert.equal(nameValue(), "Spring push");
    assert.ok(keptNotice());
});

test("another account signing in on this tab starts empty and leaves the first account's draft alone", async () => {
    mountPage(ACCOUNT_A);
    await typeName("Tenant A's campaign");
    await reload(ACCOUNT_B);
    assert.equal(nameValue(), "");
    assert.equal(keptNotice(), null);
    await reload(ACCOUNT_A);
    assert.equal(nameValue(), "Tenant A's campaign");
});

test("Start over discards the kept draft", async () => {
    mountPage(ACCOUNT_A);
    await typeName("Abandoned idea");
    await reload(ACCOUNT_A);
    fireEvent.click(screen.getByRole("button", { name: "Start over" }));
    await waitFor(() => assert.equal(nameValue(), ""));
    assert.equal(keptNotice(), null);
    await reload(ACCOUNT_A);
    assert.equal(nameValue(), "");
    assert.equal(keptNotice(), null);
});

test("creating the campaign removes its draft", async () => {
    window.sessionStorage.setItem(WIZARD_A, JSON.stringify({
        step: 0, voiceSelection: REALTIME, name: "Spring push", companyName: "Acme", agentNamesRaw: "Alex", personaType: "lead_gen",
    }));
    mountPage(ACCOUNT_A);
    const next = await screen.findByRole("button", { name: /Next: Knowledge/ }, { timeout: SLOW }) as HTMLButtonElement;
    await waitFor(() => assert.equal(next.disabled, false), { timeout: SLOW });
    fireEvent.click(next);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Next: Review/ })); });
    const create = await screen.findByRole("button", { name: "Create campaign" }, { timeout: SLOW }) as HTMLButtonElement;
    await waitFor(() => assert.equal(create.disabled, false), { timeout: SLOW });
    await act(async () => { fireEvent.click(create); });
    await waitFor(() => assert.deepEqual(pushes, ["/campaigns/campaign-1"]), { timeout: SLOW });
    assert.equal(created?.name, "Spring push");
    await new Promise((resolve) => setTimeout(resolve, 50));
    assert.equal(window.sessionStorage.getItem(WIZARD_A), null);
    await reload(ACCOUNT_A);
    assert.equal(nameValue(), "");
    assert.equal(keptNotice(), null);
});

test("the classic form keeps its own draft through a reload", async () => {
    mountPage(ACCOUNT_A);
    fireEvent.click(await screen.findByRole("button", { name: /Prefer the detailed form/ }, { timeout: SLOW }));
    const input = await screen.findByLabelText("Campaign Name", {}, { timeout: SLOW });
    fireEvent.change(input, { target: { value: "Classic draft" } });
    await reload(ACCOUNT_A);
    fireEvent.click(screen.getByRole("button", { name: /Prefer the detailed form/ }));
    assert.equal((await screen.findByLabelText("Campaign Name", {}, { timeout: SLOW }) as HTMLInputElement).value, "Classic draft");
    assert.ok(keptNotice());
});

test("a passing re-check of the sign-in keeps the editor and its text", async () => {
    const { client, view } = mountPage(ACCOUNT_A);
    await typeName("Still here");
    currentUser = null;
    view.rerender(tree(client));
    assert.equal(nameValue(), "Still here");
    currentUser = ACCOUNT_A;
    view.rerender(tree(client));
    assert.equal(nameValue(), "Still here");
});

// QA F1-21 (QA-UI-041): a read-only user could open Create Campaign and fill
// in a form the server refuses (campaigns:create).
test("a role that may not create campaigns is told so instead of shown the form", async () => {
    permissionsQuery = { isSuccess: true, data: { permissions: ["campaigns:read", "calls:read"] } };
    mountPage(ACCOUNT_A);
    await screen.findByText("Your role can't create campaigns", undefined, { timeout: SLOW });
    assert.equal(screen.queryByLabelText(/^Campaign name$/i) === null, true, "form shown");
    assert.equal(screen.queryByRole("button", { name: /Prefer the detailed form/ }) === null, true, "form toggle shown");
    assert.equal(screen.getByRole("link", { name: "View campaigns" }).getAttribute("href"), "/campaigns");
});

test("a role that may create campaigns gets the wizard", async () => {
    permissionsQuery = { isSuccess: true, data: { permissions: ["campaigns:read", "campaigns:create"] } };
    mountPage(ACCOUNT_A);
    await screen.findByLabelText(/^Campaign name$/i, {}, { timeout: SLOW });
    assert.equal(screen.queryByText("Your role can't create campaigns"), null);
});
