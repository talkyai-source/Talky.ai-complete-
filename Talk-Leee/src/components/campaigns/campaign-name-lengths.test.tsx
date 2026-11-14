import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import { CampaignBasicsEditor } from "@/components/campaigns/campaign-basics-editor";
import { CampaignForm, type CampaignFormInitial } from "@/components/campaigns/campaign-form";
import { CampaignWizard } from "@/components/campaigns/campaign-wizard";
import { aiOptionsApi } from "@/lib/ai-options-api";
import { representativeNameTooLong } from "@/lib/campaign-personas";
import { dashboardApi } from "@/lib/dashboard-api";
import { leadDetailsApi } from "@/lib/lead-details-api";

// Forms audit B-F5: the server caps the brand and the first representative
// name at 120 characters; the forms must stop it before the final save.
const SLOW = 15_000;
const REALTIME = { pipeline_mode: "realtime" as const, realtime_model: "gpt-realtime", realtime_voice: "alloy" };
const LONG = "A".repeat(121);
const router = {
    push() {}, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {},
} as unknown as React.ContextType<typeof AppRouterContext>;
const clients: QueryClient[] = [];

beforeEach(() => {
    mock.method(aiOptionsApi, "getProviders", async () => ({
        realtime: { available: true, model: "gpt-realtime", voices: [{ id: "alloy", name: "Alloy" }] },
    }));
    mock.method(aiOptionsApi, "getConfig", async () => ({ ...REALTIME, tts_provider: "cartesia" }));
    mock.method(aiOptionsApi, "getVoices", async () => ({ voices: [] }));
    mock.method(leadDetailsApi, "fieldSpec", async () => ({ fields: [], csv_template_headers: [] }));
    mock.method(leadDetailsApi, "campaignFields", async () => ({ fields: [] }));
    mock.method(dashboardApi, "previewCampaignPrompt", async () => ({ system_prompt: "", greeting: "", prompt_chars: 0, layers: [] }));
});
afterEach(() => {
    cleanup();
    clients.splice(0).forEach((client) => client.clear());
    window.sessionStorage.clear();
    mock.restoreAll();
});

function wrap(node: React.ReactNode) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.push(client);
    return render(
        <AppRouterContext.Provider value={router}>
            <QueryClientProvider client={client}>{node}</QueryClientProvider>
        </AppRouterContext.Provider>,
    );
}

test("representativeNameTooLong checks the first name against 120", () => {
    assert.equal(representativeNameTooLong(["A".repeat(120)]), false);
    assert.equal(representativeNameTooLong([LONG]), true);
    assert.equal(representativeNameTooLong([]), false);
});

test("the wizard caps the company box at 120 and blocks Next on a first name over 120", async () => {
    window.sessionStorage.setItem("w", JSON.stringify({
        step: 0, voiceSelection: REALTIME, name: "Spring", companyName: "Acme", agentNamesRaw: LONG, personaType: "lead_gen",
    }));
    wrap(<CampaignWizard draftStorageKey="w" />);
    const next = await screen.findByRole("button", { name: /Next: Knowledge/ }, { timeout: SLOW }) as HTMLButtonElement;
    assert.equal((document.getElementById("cw-company") as HTMLInputElement).maxLength, 120);
    assert.match(screen.getByRole("alert").textContent ?? "", /120 characters or fewer/);
    assert.equal(next.disabled, true);
    await act(async () => { fireEvent.change(document.getElementById("cw-agents")!, { target: { value: "Alex" } }); });
    await waitFor(() => assert.equal(next.disabled, false), { timeout: SLOW });
});

test("the basics editor caps the company box at 120 and blocks Save on a first name over 120", async () => {
    wrap(<CampaignBasicsEditor campaignId="c1" initial={{
        voiceSelection: REALTIME, name: "Spring", companyName: "Acme", personaType: "lead_gen", agentNames: [LONG], voiceId: "", goal: "",
        callingSchedule: { timezone: "Europe/London", time_window_start: "09:00", time_window_end: "17:00", allowed_days: [0] },
    }} />);
    const save = await screen.findByRole("button", { name: "Save changes" }, { timeout: SLOW }) as HTMLButtonElement;
    assert.equal((document.getElementById("ce-company") as HTMLInputElement).maxLength, 120);
    assert.equal(save.disabled, true);
});

test("the classic form caps the company box at 120 and refuses a first name over 120", async () => {
    const update = mock.method(dashboardApi, "updateCampaign", async () => { throw new Error("must not be called"); });
    const initial: CampaignFormInitial = {
        voiceSelection: REALTIME, name: "Form campaign", description: "", system_prompt: "", voice_id: "", goal: "",
        persona_type: "lead_gen", company_name: "Acme", agent_names: [LONG], slots: {},
        calling_schedule: { timezone: "Europe/London", time_window_start: "09:00", time_window_end: "17:00", allowed_days: [0] },
    };
    const { container } = wrap(<CampaignForm mode="edit" campaignId="c1" initialData={initial} />);
    const button = await screen.findByRole("button", { name: "Save campaign" }, { timeout: SLOW }) as HTMLButtonElement;
    await waitFor(() => assert.equal(button.disabled, false), { timeout: SLOW });
    assert.equal((document.getElementById("company_name") as HTMLInputElement).maxLength, 120);
    await act(async () => { fireEvent.submit(container.querySelector("form")!); });
    await waitFor(() => assert.ok(screen.getAllByText(/120 characters or fewer/).length >= 1), { timeout: SLOW });
    assert.equal(update.mock.callCount(), 0);
});
