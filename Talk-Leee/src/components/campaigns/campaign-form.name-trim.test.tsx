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

// Forms audit B-F26: a name of only spaces passed `required` and was stored empty.
test("the classic form refuses a campaign name of only spaces and trims a real one", async () => {
    const update = mock.method(dashboardApi, "updateCampaign", async () => ({ campaign: { id: "c1" } as unknown as import("@/lib/dashboard-api").Campaign }));
    mock.method(leadDetailsApi, "setCampaignFields", async () => ({ fields: [] }));
    const base: CampaignFormInitial = {
        voiceSelection: REALTIME, name: "   ", description: "", system_prompt: "", voice_id: "", goal: "",
        persona_type: "lead_gen", company_name: "Acme", agent_names: ["Alex"], slots: {},
        calling_schedule: { timezone: "Europe/London", time_window_start: "09:00", time_window_end: "17:00", allowed_days: [0] },
    };
    const { container } = wrap(<CampaignForm mode="edit" campaignId="c1" initialData={base} />);
    const button = await screen.findByRole("button", { name: "Save campaign" }, { timeout: SLOW }) as HTMLButtonElement;
    await waitFor(() => assert.equal(button.disabled, false), { timeout: SLOW });
    await act(async () => { fireEvent.submit(container.querySelector("form")!); });
    await waitFor(() => assert.ok(screen.getByText("Add a campaign name.")), { timeout: SLOW });
    assert.equal(update.mock.callCount(), 0);
    await act(async () => { fireEvent.change(container.querySelector("#name")!, { target: { value: "  Spring  " } }); });
    await act(async () => { fireEvent.submit(container.querySelector("form")!); });
    await waitFor(() => assert.equal(update.mock.callCount(), 1), { timeout: SLOW });
    assert.equal((update.mock.calls[0]!.arguments[1] as { name: string }).name, "Spring");
});
