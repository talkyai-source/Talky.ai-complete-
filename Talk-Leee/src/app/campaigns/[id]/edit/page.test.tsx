import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { aiOptionsApi } from "@/lib/ai-options-api";
import { dashboardApi, type Campaign, type CampaignUpdate } from "@/lib/dashboard-api";
import { ApiClientError } from "@/lib/http-client";
import { leadDetailsApi } from "@/lib/lead-details-api";

// QA H7 / F3-03: the edit page saved without the version it had read, so a
// second editor's save silently overwrote the first one's (last write wins).
// Both editors now send the campaign's `updated_at` back; a stale save is
// refused by the server and shown as a conflict with a reload, never retried
// or reported as saved.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const pushes: string[] = [];
let backs = 0;
const router = { push(href: string) { pushes.push(href); }, replace() {}, back() { backs++; }, refresh() {}, prefetch: async () => {} };
loader._load = function (id, ...args) {
    if (id === "next/navigation") return {
        useParams: () => ({ id: "campaign-1" }), useRouter: () => router,
        usePathname: () => "/campaigns/campaign-1/edit", useSearchParams: () => new URLSearchParams(),
    };
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
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
const OPENED = "2026-11-10T09:15:30.123456+00:00";
const AFTER_OWN_SAVE = "2026-11-10T09:20:00.654321+00:00";
const REALTIME = { pipeline_mode: "realtime" as const, realtime_model: "gpt-realtime", realtime_voice: "alloy" };
const original = {
    getCampaign: dashboardApi.getCampaign, updateCampaign: dashboardApi.updateCampaign,
    previewCampaignPrompt: dashboardApi.previewCampaignPrompt,
    getProviders: aiOptionsApi.getProviders, getConfig: aiOptionsApi.getConfig, getVoices: aiOptionsApi.getVoices,
    fieldSpec: leadDetailsApi.fieldSpec, campaignFields: leadDetailsApi.campaignFields, setCampaignFields: leadDetailsApi.setCampaignFields,
};
const clients: QueryClient[] = [];
let updates: { id: string; data: CampaignUpdate }[] = [];
let fieldSaves = 0;

function campaign(knowledgeDriven: boolean): Campaign {
    return {
        id: "campaign-1", name: "Spring push", status: "draft", direction: "outbound", system_prompt: "", voice_id: "",
        max_concurrent_calls: 1, total_leads: 0, calls_completed: 0, calls_failed: 0, created_at: "2026-11-01T00:00:00Z",
        updated_at: OPENED,
        script_config: {
            ...REALTIME, persona_type: "lead_gen", company_name: "Acme", agent_names: ["Alex"], campaign_slots: {},
            ...(knowledgeDriven ? { knowledge_driven: true } : {}),
        } as Campaign["script_config"],
    };
}
function conflict() {
    return new ApiClientError({
        status: 409, code: "campaign_version_conflict", message: "This campaign changed since you opened it. Reload before saving.",
        details: { current: { ...campaign(false), name: "Saved by someone else", updated_at: "2026-11-10T09:16:00.000001+00:00" } },
        url: "http://example.test/campaigns/campaign-1", method: "PUT",
    });
}

beforeEach(() => {
    updates = [];
    fieldSaves = 0;
    backs = 0;
    pushes.splice(0);
    aiOptionsApi.getProviders = async () => ({
        realtime: { available: true, model: "gpt-realtime", voices: [{ id: "alloy", name: "Alloy" }] },
    }) as unknown as Awaited<ReturnType<typeof aiOptionsApi.getProviders>>;
    aiOptionsApi.getConfig = async () => ({ ...REALTIME, tts_provider: "cartesia" }) as unknown as Awaited<ReturnType<typeof aiOptionsApi.getConfig>>;
    aiOptionsApi.getVoices = async () => ({ voices: [] });
    leadDetailsApi.fieldSpec = async () => ({ fields: [], csv_template_headers: [] });
    leadDetailsApi.campaignFields = async () => ({ fields: [] });
    leadDetailsApi.setCampaignFields = (async () => { fieldSaves++; return { fields: [] }; }) as unknown as typeof leadDetailsApi.setCampaignFields;
    dashboardApi.updateCampaign = async (id, data) => {
        updates.push({ id, data });
        return { campaign: { ...campaign(false), updated_at: AFTER_OWN_SAVE } };
    };
    dashboardApi.previewCampaignPrompt = (async () => ({
        system_prompt: "", greeting: "", prompt_chars: 0, layers: [],
    })) as unknown as typeof dashboardApi.previewCampaignPrompt;
});

const originalConfirm = window.confirm;
afterEach(() => {
    cleanup();
    window.confirm = originalConfirm;
    clients.splice(0).forEach((client) => client.clear());
    Object.assign(dashboardApi, { getCampaign: original.getCampaign, updateCampaign: original.updateCampaign, previewCampaignPrompt: original.previewCampaignPrompt });
    Object.assign(aiOptionsApi, { getProviders: original.getProviders, getConfig: original.getConfig, getVoices: original.getVoices });
    Object.assign(leadDetailsApi, { fieldSpec: original.fieldSpec, campaignFields: original.campaignFields, setCampaignFields: original.setCampaignFields });
});

function mountEditPage(knowledgeDriven: boolean) {
    dashboardApi.getCampaign = async () => ({ campaign: campaign(knowledgeDriven) });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.push(client);
    return render(<QueryClientProvider client={client}><Page /></QueryClientProvider>);
}

const EDITORS = [
    { label: "classic form", knowledgeDriven: false, save: /^Save Campaign$/ },
    { label: "knowledge-driven editor", knowledgeDriven: true, save: /^Save changes$/ },
];

async function save(name: RegExp) {
    const button = await screen.findByRole("button", { name }, { timeout: SLOW }) as HTMLButtonElement;
    await waitFor(() => assert.equal(button.disabled, false), { timeout: SLOW });
    await act(async () => { fireEvent.click(button); });
}

for (const editor of EDITORS) {
    test(`the ${editor.label} sends back the version the page loaded`, async () => {
        mountEditPage(editor.knowledgeDriven);
        await save(editor.save);
        await waitFor(() => assert.equal(updates.length, 1), { timeout: SLOW });
        assert.equal(updates[0].id, "campaign-1");
        assert.equal(updates[0].data.expected_updated_at, OPENED, "the exact server string, microseconds included");
        await waitFor(() => assert.deepEqual(pushes, ["/campaigns/campaign-1"]));
    });

    test(`a stale save in the ${editor.label} shows the conflict and a reload, and is neither retried nor called saved`, async () => {
        dashboardApi.updateCampaign = async (id, data) => { updates.push({ id, data }); throw conflict(); };
        mountEditPage(editor.knowledgeDriven);
        await save(editor.save);
        const alert = await screen.findByRole("alert", {}, { timeout: SLOW });
        assert.match(alert.textContent ?? "", /Someone else saved this campaign after you opened it/);
        assert.match(alert.textContent ?? "", /your changes were not saved/);
        assert.ok(screen.getByRole("button", { name: "Reload latest version" }));
        assert.equal(document.body.textContent?.includes("Campaign details were saved"), false);
        await new Promise((resolve) => setTimeout(resolve, 50));
        assert.equal(updates.length, 1, "the stale save is not retried");
        assert.equal(fieldSaves, 0, "nothing after the refused save runs");
        assert.deepEqual(pushes, []);
    });

    test(`a ${editor.label} retry after a failed field-policy save sends the version of its own save`, async () => {
        leadDetailsApi.setCampaignFields = (async () => {
            fieldSaves++;
            if (fieldSaves === 1) throw new Error("field policy unavailable");
            return { fields: [] };
        }) as unknown as typeof leadDetailsApi.setCampaignFields;
        mountEditPage(editor.knowledgeDriven);
        await save(editor.save);
        await waitFor(() => assert.ok(document.body.textContent?.includes("field policy unavailable")), { timeout: SLOW });
        await save(editor.save);
        await waitFor(() => assert.equal(updates.length, 2), { timeout: SLOW });
        assert.equal(updates[0].data.expected_updated_at, OPENED);
        assert.equal(updates[1].data.expected_updated_at, AFTER_OWN_SAVE, "its own earlier write is not a conflict");
        await waitFor(() => assert.deepEqual(pushes, ["/campaigns/campaign-1"]));
    });
}

test("a failed campaign update in the classic form is not reported as saved", async () => {
    dashboardApi.updateCampaign = async (id, data) => {
        updates.push({ id, data });
        throw new ApiClientError({ status: 500, code: "server_error", message: "Failed to update campaign", url: "http://example.test/campaigns/campaign-1", method: "PUT" });
    };
    mountEditPage(false);
    await save(/^Save Campaign$/);
    await waitFor(() => assert.ok(document.body.textContent?.includes("Failed to update campaign")), { timeout: SLOW });
    assert.equal(document.body.textContent?.includes("Campaign details were saved"), false);
    assert.equal(fieldSaves, 0);
});

// QA F1-22 (QA-UI-042/094): leaving the edit page through the sidebar, a
// reload or Cancel dropped unsaved edits without a word.
function answerConfirm(answer: boolean) {
    const asked: string[] = [];
    window.confirm = ((message?: string) => { asked.push(String(message)); return answer; }) as typeof window.confirm;
    return asked;
}
/** Clicks an in-app link (the sidebar's kind); true when the navigation went ahead. */
function followAppLink() {
    const link = document.createElement("a");
    link.setAttribute("href", "/campaigns");
    document.body.appendChild(link);
    let wentAhead = false;
    // Stands in for the router: jsdom cannot navigate.
    const router = (event: Event) => { wentAhead = true; event.preventDefault(); };
    document.addEventListener("click", router);
    link.dispatchEvent(new window.MouseEvent("click", { bubbles: true, cancelable: true, button: 0 }));
    document.removeEventListener("click", router);
    link.remove();
    return wentAhead;
}
function reloadAsks() {
    const event = new window.Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    return event.defaultPrevented;
}
async function editName(value: string) {
    const input = await screen.findByLabelText(/^Campaign name$/i, {}, { timeout: SLOW });
    fireEvent.pointerDown(input);
    fireEvent.change(input, { target: { value } });
}
async function loaded(editor: (typeof EDITORS)[number]) {
    const button = await screen.findByRole("button", { name: editor.save }, { timeout: SLOW }) as HTMLButtonElement;
    await waitFor(() => assert.equal(button.disabled, false), { timeout: SLOW });
}

for (const editor of EDITORS) {
    test(`an edited ${editor.label} asks before an in-app link or a reload discards the edits`, async () => {
        mountEditPage(editor.knowledgeDriven);
        await loaded(editor);
        await editName("Renamed but not saved");
        const asked = answerConfirm(false);
        assert.equal(followAppLink(), false, "declining keeps the page");
        assert.equal(asked.length, 1);
        assert.match(asked[0], /unsaved changes/);
        assert.equal(reloadAsks(), true, "a reload or close asks first");
        answerConfirm(true);
        assert.equal(followAppLink(), true, "agreeing leaves");
    });

    test(`an untouched ${editor.label} leaves without asking`, async () => {
        mountEditPage(editor.knowledgeDriven);
        await loaded(editor);
        await new Promise((resolve) => setTimeout(resolve, 50));
        const asked = answerConfirm(false);
        assert.equal(followAppLink(), true);
        assert.equal(reloadAsks(), false);
        assert.deepEqual(asked, []);
    });

    test(`Cancel on an edited ${editor.label} asks first and stays when declined`, async () => {
        mountEditPage(editor.knowledgeDriven);
        await loaded(editor);
        await editName("Renamed but not saved");
        const asked = answerConfirm(false);
        fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
        assert.equal(asked.length, 1);
        assert.deepEqual(pushes, []);
        assert.equal(backs, 0);
    });

    test(`saving an edited ${editor.label} leaves without asking`, async () => {
        mountEditPage(editor.knowledgeDriven);
        await loaded(editor);
        await editName("Renamed and saved");
        const asked = answerConfirm(false);
        await save(editor.save);
        await waitFor(() => assert.deepEqual(pushes, ["/campaigns/campaign-1"]), { timeout: SLOW });
        assert.equal(updates[0].data.name, "Renamed and saved");
        assert.equal(reloadAsks(), false);
        assert.deepEqual(asked, []);
    });
}

test("the edit page's way back is a link to the campaign, so the guard covers it", async () => {
    mountEditPage(false);
    const back = await screen.findByRole("link", { name: /Back to campaign/ }, { timeout: SLOW });
    assert.equal(back.getAttribute("href"), "/campaigns/campaign-1");
});
