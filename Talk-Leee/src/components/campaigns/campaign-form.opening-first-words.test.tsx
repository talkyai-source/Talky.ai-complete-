import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import { CampaignForm, type CampaignFormInitial } from "@/components/campaigns/campaign-form";
import { aiOptionsApi } from "@/lib/ai-options-api";
import { dashboardApi } from "@/lib/dashboard-api";
import { leadDetailsApi } from "@/lib/lead-details-api";

const SLOW = 15_000;
const REALTIME = { pipeline_mode: "realtime" as const, realtime_model: "gpt-realtime", realtime_voice: "alloy" };
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
    mock.method(dashboardApi, "previewCampaignPrompt", async () => ({
        system_prompt: "", greeting: "Plain greeting", direction: "outbound", opening_mode: "agent_first", has_inbound_directive: false,
        prompt_chars: 0, campaign_guidance_chars: 0, campaign_guidance_budget_chars: 4000, over_budget: false, layers: [],
        outbound_first_words: { notice_first: "Our own notice.", opening: "Hi, this is Grace from Acme.", notice_in_opening: false },
    }));
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

test("the classic form's preview shows the recording notice first, then the opening", async () => {
    const base: CampaignFormInitial = {
        voiceSelection: REALTIME, name: "Spring", description: "", system_prompt: "", voice_id: "", goal: "",
        persona_type: "lead_gen", company_name: "Acme", agent_names: ["Alex"], slots: {},
        calling_schedule: { timezone: "Europe/London", time_window_start: "09:00", time_window_end: "17:00", allowed_days: [0] },
    };
    wrap(<CampaignForm mode="edit" campaignId="c1" initialData={base} />);
    await act(async () => { fireEvent.click(await screen.findByRole("button", { name: "Show preview" }, { timeout: SLOW })); });
    const render = await screen.findByRole("button", { name: "Render preview" }, { timeout: SLOW }) as HTMLButtonElement;
    await waitFor(() => assert.equal(render.disabled, false), { timeout: SLOW });
    await act(async () => { fireEvent.click(render); });
    await screen.findByText('First, your recording notice, on its own: "Our own notice."', undefined, { timeout: SLOW });
    screen.getByText('Then the opening: "Hi, this is Grace from Acme."');
    screen.getByText(/Am I speaking with \[first name\]/);
});
