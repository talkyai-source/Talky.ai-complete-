/**
 * FX1a Task 2 — a failed PUT /campaigns/{id} never says "saved". The edit form
 * shows the server's own message; the "details were saved" sentence is kept
 * for the one case it is true (details accepted, contact-field policy refused).
 */
import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import { CampaignForm, type CampaignFormInitial } from "@/components/campaigns/campaign-form";
import { aiOptionsApi } from "@/lib/ai-options-api";
import { dashboardApi, type Campaign } from "@/lib/dashboard-api";
import { leadDetailsApi } from "@/lib/lead-details-api";

const SLOW = 15_000;
const SERVER_MESSAGE = "Campaign name is already in use.";
const CAMPAIGN = { id: "campaign-1", name: "AC campaign", status: "draft", direction: "outbound" } as unknown as Campaign;
const pushes: string[] = [];
const router = {
    push(href: string) { pushes.push(href); }, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {},
} as unknown as React.ContextType<typeof AppRouterContext>;
const clients: QueryClient[] = [];

beforeEach(() => {
    pushes.splice(0);
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

const initial: CampaignFormInitial = {
    voiceSelection: { pipeline_mode: "realtime", realtime_model: "gpt-realtime", realtime_voice: "alloy" },
    name: "Form campaign", description: "", system_prompt: "", voice_id: "", goal: "",
    persona_type: "lead_gen", company_name: "Acme", agent_names: ["Alex"], slots: {},
};

async function saveEditForm() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.push(client);
    const { container } = render(
        <AppRouterContext.Provider value={router}>
            <QueryClientProvider client={client}>
                <CampaignForm mode="edit" campaignId="campaign-1" initialData={initial} />
            </QueryClientProvider>
        </AppRouterContext.Provider>,
    );
    const button = await screen.findByRole("button", { name: "Save campaign" }, { timeout: SLOW }) as HTMLButtonElement;
    await waitFor(() => assert.equal(button.disabled, false), { timeout: SLOW });
    await act(async () => { fireEvent.submit(container.querySelector("form")!); });
}

test("a failed PUT /campaigns/{id} shows the server's message and never the 'were saved' sentence", async () => {
    const update = mock.method(dashboardApi, "updateCampaign", async () => { throw new Error(SERVER_MESSAGE); });
    const fields = mock.method(leadDetailsApi, "setCampaignFields", async () => ({ fields: [] }));
    await saveEditForm();
    await waitFor(() => assert.equal(screen.queryByText(SERVER_MESSAGE) !== null, true), { timeout: SLOW });
    assert.equal(update.mock.callCount(), 1);
    assert.equal(screen.queryByText(/were saved/) === null, true, "a failed save must not say saved");
    assert.equal(fields.mock.callCount(), 0);
    assert.deepEqual(pushes, []);
});

test("details saved but contact-field policy refused keeps today's sentence", async () => {
    mock.method(dashboardApi, "updateCampaign", async () => ({ campaign: CAMPAIGN }));
    mock.method(leadDetailsApi, "setCampaignFields", async () => { throw new Error("Policy store unavailable."); });
    await saveEditForm();
    await waitFor(() => assert.equal(
        screen.queryByText(/Campaign details were saved, but its contact-field policy was not\..*Policy store unavailable\./) !== null, true,
    ), { timeout: SLOW });
    assert.deepEqual(pushes, []);
});

test("a successful save still goes to the campaign page", async () => {
    mock.method(dashboardApi, "updateCampaign", async () => ({ campaign: CAMPAIGN }));
    mock.method(leadDetailsApi, "setCampaignFields", async () => ({ fields: [] }));
    await saveEditForm();
    await waitFor(() => assert.deepEqual(pushes, ["/campaigns/campaign-1"]), { timeout: SLOW });
    assert.equal(screen.queryByText(/were saved/) === null, true);
});
