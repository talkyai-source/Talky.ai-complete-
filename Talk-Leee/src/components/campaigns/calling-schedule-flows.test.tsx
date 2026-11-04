/**
 * Production-readiness 2.3 — the calling schedule travels intact through the
 * three editors (wizard, classic form, basics editor) and comes back on the
 * edit and detail surfaces exactly as saved (TZ1, TZ2, AC-TZ).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import { CampaignWizard } from "@/components/campaigns/campaign-wizard";
import { CampaignForm, type CampaignFormInitial } from "@/components/campaigns/campaign-form";
import { CampaignBasicsEditor } from "@/components/campaigns/campaign-basics-editor";
import { CallingScheduleStartNotice } from "@/components/campaigns/calling-schedule-notice";
import { aiOptionsApi } from "@/lib/ai-options-api";
import { dashboardApi, type Campaign, type CampaignCallingSchedule, type CampaignCreate } from "@/lib/dashboard-api";
import { leadDetailsApi } from "@/lib/lead-details-api";

type Stubs = {
    getProviders: typeof aiOptionsApi.getProviders;
    getConfig: typeof aiOptionsApi.getConfig;
    getVoices: typeof aiOptionsApi.getVoices;
    fieldSpec: typeof leadDetailsApi.fieldSpec;
    campaignFields: typeof leadDetailsApi.campaignFields;
    setCampaignFields: typeof leadDetailsApi.setCampaignFields;
    createCampaign: typeof dashboardApi.createCampaign;
    updateCampaign: typeof dashboardApi.updateCampaign;
    getCampaign: typeof dashboardApi.getCampaign;
    previewCampaignPrompt: typeof dashboardApi.previewCampaignPrompt;
};
const original: Stubs = {
    getProviders: aiOptionsApi.getProviders,
    getConfig: aiOptionsApi.getConfig,
    getVoices: aiOptionsApi.getVoices,
    fieldSpec: leadDetailsApi.fieldSpec,
    campaignFields: leadDetailsApi.campaignFields,
    setCampaignFields: leadDetailsApi.setCampaignFields,
    createCampaign: dashboardApi.createCampaign,
    updateCampaign: dashboardApi.updateCampaign,
    getCampaign: dashboardApi.getCampaign,
    previewCampaignPrompt: dashboardApi.previewCampaignPrompt,
};

const BROWSER_TZ = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
const REALTIME = { pipeline_mode: "realtime" as const, realtime_model: "gpt-realtime", realtime_voice: "alloy" };
const LONDON: CampaignCallingSchedule = {
    timezone: "Europe/London", time_window_start: "09:00", time_window_end: "17:00", allowed_days: [0, 1, 2, 3, 4], ignore_schedule: false,
};
const DEFAULTS: CampaignCallingSchedule = {
    timezone: BROWSER_TZ, time_window_start: "09:00", time_window_end: "19:00", allowed_days: [0, 1, 2, 3, 4], ignore_schedule: false,
};
const CAMPAIGN = { id: "campaign-1", name: "AC campaign", status: "draft", direction: "outbound" } as unknown as Campaign;

const pushes: string[] = [];
const router = {
    push(href: string) { pushes.push(href); }, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {},
} as unknown as React.ContextType<typeof AppRouterContext>;
const clients: QueryClient[] = [];
let created: CampaignCreate | null = null;
let updated: { id: string; data: CampaignCreate } | null = null;

beforeEach(() => {
    created = null;
    updated = null;
    pushes.splice(0);
    aiOptionsApi.getProviders = async () => ({
        realtime: { available: true, model: "gpt-realtime", voices: [{ id: "alloy", name: "Alloy" }] },
    }) as unknown as Awaited<ReturnType<typeof aiOptionsApi.getProviders>>;
    aiOptionsApi.getConfig = async () => ({
        pipeline_mode: "realtime", realtime_model: "gpt-realtime", realtime_voice: "alloy", tts_provider: "cartesia",
    }) as unknown as Awaited<ReturnType<typeof aiOptionsApi.getConfig>>;
    aiOptionsApi.getVoices = async () => ({ voices: [] });
    leadDetailsApi.fieldSpec = async () => ({ fields: [], csv_template_headers: [] });
    leadDetailsApi.campaignFields = async () => ({ fields: [] });
    leadDetailsApi.setCampaignFields = (async () => ({ fields: [] })) as unknown as typeof leadDetailsApi.setCampaignFields;
    dashboardApi.createCampaign = async (data) => { created = data; return { campaign: CAMPAIGN }; };
    dashboardApi.updateCampaign = async (id, data) => { updated = { id, data }; return { campaign: CAMPAIGN }; };
    dashboardApi.previewCampaignPrompt = (async () => ({
        system_prompt: "", greeting: "", prompt_chars: 0, layers: [],
    })) as unknown as typeof dashboardApi.previewCampaignPrompt;
});

afterEach(() => {
    cleanup();
    clients.splice(0).forEach((client) => client.clear());
    window.sessionStorage.clear();
    Object.assign(aiOptionsApi, { getProviders: original.getProviders, getConfig: original.getConfig, getVoices: original.getVoices });
    Object.assign(leadDetailsApi, { fieldSpec: original.fieldSpec, campaignFields: original.campaignFields, setCampaignFields: original.setCampaignFields });
    Object.assign(dashboardApi, {
        createCampaign: original.createCampaign, updateCampaign: original.updateCampaign,
        getCampaign: original.getCampaign, previewCampaignPrompt: original.previewCampaignPrompt,
    });
});

function mount(children: React.ReactNode) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.push(client);
    return render(
        <AppRouterContext.Provider value={router}>
            <QueryClientProvider client={client}>{children}</QueryClientProvider>
        </AppRouterContext.Provider>,
    );
}

const formInitial = (over: Partial<CampaignFormInitial> = {}): CampaignFormInitial => ({
    voiceSelection: REALTIME,
    name: "Form campaign",
    description: "",
    system_prompt: "",
    voice_id: "",
    goal: "",
    persona_type: "lead_gen",
    company_name: "Acme",
    agent_names: ["Alex"],
    slots: {},
    ...over,
});

const timezoneTrigger = () => screen.getByRole("combobox", { name: "Calling timezone" });
const startInput = () => screen.getByLabelText("Calling start time") as HTMLInputElement;
const endInput = () => screen.getByLabelText("Calling end time") as HTMLInputElement;

async function pickTimezone(label: string) {
    fireEvent.click(timezoneTrigger());
    fireEvent.click(await screen.findByRole("option", { name: label }));
}

async function submitForm(container: HTMLElement) {
    const button = screen.getByRole("button", { name: /Create Campaign|Save Campaign/ }) as HTMLButtonElement;
    await waitFor(() => assert.equal(button.disabled, false));
    await act(async () => { fireEvent.submit(container.querySelector("form")!); });
}

function expectSavedLondonShown() {
    assert.equal(timezoneTrigger().textContent?.trim(), "UK (London)");
    assert.equal(startInput().value, "09:00");
    assert.equal(endInput().value, "17:00");
    for (const day of ["Mon", "Tue", "Wed", "Thu", "Fri"]) {
        assert.equal(screen.getByRole("button", { name: day }).getAttribute("aria-pressed"), "true");
    }
    assert.ok(!screen.queryByTestId("calling-schedule-missing"));
}

test("TZ1 wizard: the create payload carries all four schedule fields as shown (browser zone, 09:00–19:00, Mon–Fri)", async () => {
    window.sessionStorage.setItem("wizard-tz", JSON.stringify({
        step: 0, voiceSelection: REALTIME, name: "AC campaign", companyName: "Acme", agentNamesRaw: "Alex", personaType: "lead_gen",
    }));
    mount(<CampaignWizard draftStorageKey="wizard-tz" />);
    await waitFor(() => assert.equal((screen.getByRole("button", { name: /Next: Knowledge/ }) as HTMLButtonElement).disabled, false));
    assert.equal(startInput().value, "09:00");
    assert.equal(endInput().value, "19:00");
    fireEvent.click(screen.getByRole("button", { name: /Next: Knowledge/ }));
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Next: Review/ })); });
    const create = await screen.findByRole("button", { name: "Create campaign" }) as HTMLButtonElement;
    await waitFor(() => assert.equal(create.disabled, false));
    await act(async () => { fireEvent.click(create); });
    await waitFor(() => assert.ok(created));
    assert.deepEqual(created!.calling_schedule, DEFAULTS);
});

test("TZ1 classic form (create): the payload carries all four schedule fields as shown", async () => {
    const { container } = mount(<CampaignForm mode="create" initialData={formInitial()} />);
    await waitFor(() => assert.ok(screen.getByText("Calling hours")));
    assert.equal(startInput().value, "09:00");
    assert.equal(endInput().value, "19:00");
    await submitForm(container);
    await waitFor(() => assert.ok(created));
    assert.deepEqual(created!.calling_schedule, DEFAULTS);
    assert.ok(!updated);
});

test("TZ1/TZ2 classic form (edit): the saved schedule is loaded and resent in full on save", async () => {
    const { container } = mount(<CampaignForm mode="edit" campaignId="campaign-1" direction="outbound" initialData={formInitial({ calling_schedule: LONDON })} />);
    await waitFor(() => assert.ok(screen.getByText("Calling hours")));
    expectSavedLondonShown();
    await submitForm(container);
    await waitFor(() => assert.ok(updated));
    assert.equal(updated!.id, "campaign-1");
    assert.deepEqual(updated!.data.calling_schedule, LONDON);
});

test("TZ2/TZ4 classic form (edit): null saved fields read Not set and are saved as null, never as the browser zone or 09:00/19:00", async () => {
    const { container } = mount(<CampaignForm mode="edit" campaignId="campaign-1" direction="outbound" initialData={formInitial({ calling_schedule: null })} />);
    await waitFor(() => assert.ok(screen.getByText("Calling hours")));
    assert.equal(timezoneTrigger().textContent?.trim(), "Not set");
    assert.ok(!timezoneTrigger().textContent?.includes(BROWSER_TZ));
    assert.equal(startInput().value, "");
    assert.equal(endInput().value, "");
    assert.equal(screen.getAllByText("Not set").length, 4);
    assert.ok(screen.getByTestId("calling-schedule-missing"));
    await submitForm(container);
    await waitFor(() => assert.ok(updated));
    assert.deepEqual(updated!.data.calling_schedule, {
        timezone: null, time_window_start: null, time_window_end: null, allowed_days: null, ignore_schedule: false,
    });
});

test("scope: an inbound campaign edited in the classic form gets no schedule editor and sends no calling_schedule", async () => {
    const { container } = mount(<CampaignForm mode="edit" campaignId="campaign-1" direction="inbound" initialData={formInitial()} />);
    const save = screen.getByRole("button", { name: "Save Campaign" }) as HTMLButtonElement;
    await waitFor(() => assert.equal(save.disabled, false));
    assert.ok(!screen.queryByText("Calling hours"));
    await submitForm(container);
    await waitFor(() => assert.ok(updated));
    assert.ok(!("calling_schedule" in updated!.data));
});

test("TZ1 basics editor: changing one schedule field sends all four, with the unset ones as null", async () => {
    mount(<CampaignBasicsEditor campaignId="campaign-1" initial={{
        voiceSelection: REALTIME, name: "KB campaign", companyName: "Acme", personaType: "lead_gen", agentNames: ["Alex"], voiceId: "", goal: "",
        callingSchedule: { timezone: "Europe/London" },
    }} />);
    assert.equal(timezoneTrigger().textContent?.trim(), "UK (London)");
    assert.equal(startInput().value, "");
    assert.ok(screen.getByTestId("calling-start-not-set"));
    assert.ok(screen.getByTestId("calling-days-not-set"));
    fireEvent.change(endInput(), { target: { value: "17:00" } });
    const save = screen.getByRole("button", { name: "Save changes" }) as HTMLButtonElement;
    await waitFor(() => assert.equal(save.disabled, false));
    await act(async () => { fireEvent.click(save); });
    await waitFor(() => assert.ok(updated));
    assert.deepEqual(updated!.data.calling_schedule, {
        timezone: "Europe/London", time_window_start: null, time_window_end: "17:00", allowed_days: null, ignore_schedule: false,
    });
});

test("AC-TZ: Europe/London 09:00–17:00 Mon–Fri is created, read back as calling_config, and shown on edit and detail", async () => {
    // 1. Create in the wizard with the schedule chosen in the editor.
    window.sessionStorage.setItem("wizard-ac", JSON.stringify({
        step: 0, voiceSelection: REALTIME, name: "AC campaign", companyName: "Acme", agentNamesRaw: "Alex", personaType: "lead_gen",
    }));
    const wizard = mount(<CampaignWizard draftStorageKey="wizard-ac" />);
    await waitFor(() => assert.equal((screen.getByRole("button", { name: /Next: Knowledge/ }) as HTMLButtonElement).disabled, false));
    await pickTimezone("UK (London)");
    fireEvent.change(endInput(), { target: { value: "17:00" } });
    fireEvent.click(screen.getByRole("button", { name: /Next: Knowledge/ }));
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Next: Review/ })); });
    const create = await screen.findByRole("button", { name: "Create campaign" }) as HTMLButtonElement;
    await waitFor(() => assert.equal(create.disabled, false));
    await act(async () => { fireEvent.click(create); });
    await waitFor(() => assert.ok(created));
    assert.deepEqual(created!.calling_schedule, LONDON);
    wizard.unmount();

    // 2. The same values come back as GET /campaigns/{id} → calling_config.
    dashboardApi.getCampaign = async () => ({ campaign: { ...CAMPAIGN, calling_config: created!.calling_schedule } });
    const { campaign } = await dashboardApi.getCampaign("campaign-1");
    assert.deepEqual(campaign.calling_config, LONDON);

    // 3. Edit page: the knowledge-driven editor (wizard campaigns) and the
    //    classic form both show what was saved.
    const basics = mount(<CampaignBasicsEditor campaignId="campaign-1" initial={{
        voiceSelection: REALTIME, name: campaign.name, companyName: "Acme", personaType: "lead_gen", agentNames: ["Alex"], voiceId: "", goal: "",
        callingSchedule: campaign.calling_config,
    }} />);
    expectSavedLondonShown();
    basics.unmount();
    const classic = mount(<CampaignForm mode="edit" campaignId="campaign-1" direction="outbound" initialData={formInitial({ calling_schedule: campaign.calling_config })} />);
    await waitFor(() => assert.ok(screen.getByText("Calling hours")));
    expectSavedLondonShown();
    classic.unmount();

    // 4. Detail page: the read-only line in the Start dialog.
    mount(<CallingScheduleStartNotice callingConfig={campaign.calling_config} />);
    assert.equal(screen.getByTestId("calling-schedule-line").textContent, "Calling schedule: Europe/London · 09:00–17:00 · Mon, Tue, Wed, Thu, Fri");
    assert.ok(!screen.queryByTestId("calling-schedule-missing"));
});

test("AC-TZ wiring: the edit page feeds calling_config to both editors and the detail page mounts the notice", () => {
    const edit = readFileSync(path.join(process.cwd(), "src/app/campaigns/[id]/edit/page.tsx"), "utf8");
    const detail = readFileSync(path.join(process.cwd(), "src/app/campaigns/[id]/page.tsx"), "utf8");
    assert.match(edit, /calling_schedule: \(campaign as \{ calling_config\?: CampaignCallingSchedule \| null \}\)\.calling_config \?\? null,/);
    assert.match(edit, /callingSchedule,\n/);
    assert.match(edit, /<CampaignForm mode="edit" campaignId=\{campaignId\} initialData=\{initial\} direction=\{direction\} \/>/);
    assert.match(detail, /<CallingScheduleStartNotice callingConfig=\{campaign\?\.calling_config\} \/>/);
});
