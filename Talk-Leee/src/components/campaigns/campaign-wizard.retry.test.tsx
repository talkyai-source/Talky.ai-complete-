import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import { CampaignWizard } from "@/components/campaigns/campaign-wizard";
import { aiOptionsApi } from "@/lib/ai-options-api";
import { dashboardApi, type Campaign } from "@/lib/dashboard-api";
import { leadDetailsApi } from "@/lib/lead-details-api";

// Forms audit B-F13: after a partial failure (campaign created, contact-field
// save failed) a retry must send the CURRENT step-1 values, not leave the
// campaign with the first attempt's values.
const SLOW = 15_000;
const REALTIME = { pipeline_mode: "realtime" as const, realtime_model: "gpt-realtime", realtime_voice: "alloy" };
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

test("a retry with an existing campaign updates it with the current values before saving the contact fields", async () => {
    // The draft is what the wizard holds after a first attempt that created c1.
    window.sessionStorage.setItem("w", JSON.stringify({
        step: 2, voiceSelection: REALTIME, name: "Renamed after failure", companyName: "Acme", agentNamesRaw: "Alex",
        personaType: "lead_gen", createdCampaignId: "c1",
        schedule: { timezone: "Europe/London", time_window_start: "09:00", time_window_end: "17:00", allowed_days: [0] },
    }));
    const create = mock.method(dashboardApi, "createCampaign", async () => ({ campaign: { id: "new" } as unknown as Campaign }));
    const update = mock.method(dashboardApi, "updateCampaign", async () => ({ campaign: { id: "c1" } as unknown as Campaign }));
    const order: string[] = [];
    update.mock.mockImplementation(async () => { order.push("update"); return { campaign: { id: "c1" } as unknown as Campaign }; });
    mock.method(leadDetailsApi, "setCampaignFields", async () => { order.push("fields"); return { fields: [] }; });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.push(client);
    render(
        <AppRouterContext.Provider value={router}>
            <QueryClientProvider client={client}><CampaignWizard draftStorageKey="w" /></QueryClientProvider>
        </AppRouterContext.Provider>,
    );
    const button = await screen.findByRole("button", { name: /Create campaign/ }, { timeout: SLOW }) as HTMLButtonElement;
    await waitFor(() => assert.equal(button.disabled, false), { timeout: SLOW });
    await act(async () => { fireEvent.click(button); });
    await waitFor(() => assert.deepEqual(pushes, ["/campaigns/c1"]), { timeout: SLOW });
    assert.equal(create.mock.callCount(), 0);
    assert.equal(update.mock.callCount(), 1);
    const [id, body] = update.mock.calls[0]!.arguments as [string, { name: string; calling_schedule?: { time_window_end?: string } }];
    assert.equal(id, "c1");
    assert.equal(body.name, "Renamed after failure");
    assert.equal(body.calling_schedule?.time_window_end, "17:00");
    assert.deepEqual(order, ["update", "fields"]);
});
