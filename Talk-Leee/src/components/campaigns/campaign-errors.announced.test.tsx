/**
 * FX2a Task 1.4 — the create wizard's error and the campaign form's error are
 * announced (role="alert"); their text is unchanged.
 */
import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import { CampaignForm, type CampaignFormInitial } from "@/components/campaigns/campaign-form";
import { CampaignWizard } from "@/components/campaigns/campaign-wizard";
import { aiOptionsApi } from "@/lib/ai-options-api";
import { dashboardApi } from "@/lib/dashboard-api";
import { leadDetailsApi } from "@/lib/lead-details-api";

const SLOW = 15_000;
const router = { push() {}, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } as unknown as React.ContextType<typeof AppRouterContext>;
const clients: QueryClient[] = [];

beforeEach(() => {
    mock.method(aiOptionsApi, "getProviders", async () => ({
        realtime: { available: true, model: "gpt-realtime", voices: [{ id: "alloy", name: "Alloy" }] },
    }));
    mock.method(aiOptionsApi, "getConfig", async () => ({
        pipeline_mode: "realtime", realtime_model: "gpt-realtime", realtime_voice: "alloy", tts_provider: "cartesia",
    }));
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

function mount(node: React.ReactElement) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { gcTime: Infinity } } });
    clients.push(client);
    return render(<AppRouterContext.Provider value={router}><QueryClientProvider client={client}>{node}</QueryClientProvider></AppRouterContext.Provider>);
}

test("the campaign form's error is announced with role=alert and keeps the server's text", async () => {
    const initial: CampaignFormInitial = {
        voiceSelection: { pipeline_mode: "realtime", realtime_model: "gpt-realtime", realtime_voice: "alloy" },
        name: "Form campaign", description: "", system_prompt: "", voice_id: "", goal: "",
        persona_type: "lead_gen", company_name: "Acme", agent_names: ["Alex"], slots: {},
    };
    mock.method(dashboardApi, "updateCampaign", async () => { throw new Error("Campaign name is already in use."); });
    const { container } = mount(<CampaignForm mode="edit" campaignId="campaign-1" initialData={initial} />);
    const button = await screen.findByRole("button", { name: "Save campaign" }, { timeout: SLOW }) as HTMLButtonElement;
    await waitFor(() => assert.equal(button.disabled, false), { timeout: SLOW });
    await act(async () => { fireEvent.submit(container.querySelector("form")!); });

    await waitFor(() => assert.equal(screen.queryByText("Campaign name is already in use.") !== null, true), { timeout: SLOW });
    assert.equal(screen.getByText("Campaign name is already in use.").getAttribute("role"), "alert");
});

test("the create wizard's error is announced with role=alert and keeps its text", async () => {
    // The wizard reopens on its Knowledge step, where the file picker lives.
    window.sessionStorage.setItem("fx2a-wizard", JSON.stringify({ step: 1 }));
    const { container } = mount(<CampaignWizard draftStorageKey="fx2a-wizard" />);
    const input = await waitFor(() => {
        const found = container.querySelector<HTMLInputElement>('input[type="file"]');
        assert.equal(found !== null, true);
        return found!;
    }, { timeout: SLOW });
    assert.equal(screen.queryByRole("alert") === null, true, "no error before one happens");
    fireEvent.change(input, { target: { files: [new File(["x"], "notes.pdf", { type: "application/pdf" })] } });

    await waitFor(() => assert.equal(screen.queryByText("Please choose a .md or .txt file.") !== null, true), { timeout: SLOW });
    assert.equal(screen.getByText("Please choose a .md or .txt file.").getAttribute("role"), "alert");
});
