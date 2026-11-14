import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import { CampaignWizard } from "@/components/campaigns/campaign-wizard";
import { aiOptionsApi } from "@/lib/ai-options-api";
import { dashboardApi } from "@/lib/dashboard-api";
import { leadDetailsApi } from "@/lib/lead-details-api";

// Forms audit B-F16: required fields are marked and the disabled Next says
// what is still missing.
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

function mount(draft: Record<string, unknown>) {
    window.sessionStorage.setItem("w", JSON.stringify({ step: 0, voiceSelection: REALTIME, personaType: "lead_gen", ...draft }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.push(client);
    render(
        <AppRouterContext.Provider value={router}>
            <QueryClientProvider client={client}><CampaignWizard draftStorageKey="w" /></QueryClientProvider>
        </AppRouterContext.Provider>,
    );
}

test("name, brand and representative labels carry a required marker", async () => {
    mount({});
    await screen.findByRole("button", { name: /Next: Knowledge/ }, { timeout: SLOW });
    for (const id of ["cw-name", "cw-company", "cw-agents"]) {
        const label = document.querySelector(`label[for="${id}"]`);
        assert.ok(label?.textContent?.includes("*"), `${id} label has no required marker`);
    }
});

test("the disabled Next lists what is missing and the line goes away once it is filled", async () => {
    mount({ name: "Spring" });
    const next = await screen.findByRole("button", { name: /Next: Knowledge/ }, { timeout: SLOW }) as HTMLButtonElement;
    assert.equal(next.disabled, true);
    const hint = screen.getByTestId("cw-next-missing").textContent ?? "";
    assert.match(hint, /brand \/ company name/);
    assert.match(hint, /representative name/);
    assert.doesNotMatch(hint, /campaign name/);
    await act(async () => {
        fireEvent.change(document.getElementById("cw-company")!, { target: { value: "Acme" } });
        fireEvent.change(document.getElementById("cw-agents")!, { target: { value: "Alex" } });
    });
    await waitFor(() => assert.equal(next.disabled, false), { timeout: SLOW });
    assert.equal(screen.queryByTestId("cw-next-missing"), null);
});
