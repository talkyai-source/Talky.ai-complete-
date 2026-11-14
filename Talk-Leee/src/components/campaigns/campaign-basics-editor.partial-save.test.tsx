import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import { CampaignBasicsEditor } from "@/components/campaigns/campaign-basics-editor";
import { aiOptionsApi } from "@/lib/ai-options-api";
import { dashboardApi, type Campaign } from "@/lib/dashboard-api";
import { leadDetailsApi } from "@/lib/lead-details-api";

// Forms audit B-F28: when the campaign saved but the contact-field policy did
// not, the message must say the campaign was saved (and a plain failure must not).
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
});
afterEach(() => {
    cleanup();
    clients.splice(0).forEach((client) => client.clear());
    mock.restoreAll();
});

async function save() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.push(client);
    render(
        <AppRouterContext.Provider value={router}>
            <QueryClientProvider client={client}>
                <CampaignBasicsEditor campaignId="c1" initial={{
                    voiceSelection: REALTIME, name: "Spring", companyName: "Acme", personaType: "lead_gen", agentNames: ["Alex"], voiceId: "", goal: "",
                    callingSchedule: { timezone: "Europe/London", time_window_start: "09:00", time_window_end: "17:00", allowed_days: [0] },
                }} />
            </QueryClientProvider>
        </AppRouterContext.Provider>,
    );
    const button = await screen.findByRole("button", { name: "Save changes" }, { timeout: SLOW }) as HTMLButtonElement;
    await waitFor(() => assert.equal(button.disabled, false), { timeout: SLOW });
    await act(async () => { fireEvent.click(button); });
}

test("campaign saved but contact-field policy refused: the message says the campaign was saved", async () => {
    mock.method(dashboardApi, "updateCampaign", async () => ({ campaign: { id: "c1" } as unknown as Campaign }));
    mock.method(leadDetailsApi, "setCampaignFields", async () => { throw new Error("Policy store unavailable."); });
    await save();
    await waitFor(() => assert.ok(
        screen.getByText(/Campaign details were saved, but its contact-field policy was not\..*Policy store unavailable\./),
    ), { timeout: SLOW });
});

test("a failed campaign save shows only the server's message", async () => {
    mock.method(dashboardApi, "updateCampaign", async () => { throw new Error("Name already in use."); });
    mock.method(leadDetailsApi, "setCampaignFields", async () => ({ fields: [] }));
    await save();
    await waitFor(() => assert.ok(screen.getByText("Name already in use.")), { timeout: SLOW });
    assert.equal(screen.queryByText(/were saved/), null);
});
