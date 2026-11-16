import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import { CampaignForm } from "@/components/campaigns/campaign-form";
import { CampaignWizard } from "@/components/campaigns/campaign-wizard";
import { aiOptionsApi } from "@/lib/ai-options-api";
import { PERSONAS } from "@/lib/campaign-personas";
import { dashboardApi } from "@/lib/dashboard-api";
import { leadDetailsApi } from "@/lib/lead-details-api";

// Live test, 14 Nov 2026: "Create inbound campaign" opened with "Lead
// Generation" selected, whose own summary says "Outbound calls". A new inbound
// campaign starts on an inbound persona; a saved choice is never overridden.
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
    render(
        <AppRouterContext.Provider value={router}>
            <QueryClientProvider client={client}>{node}</QueryClientProvider>
        </AppRouterContext.Provider>,
    );
}

async function wizardPersona(direction: "inbound" | "outbound", draft: Record<string, unknown> = {}) {
    window.sessionStorage.setItem("w", JSON.stringify({ step: 0, voiceSelection: REALTIME, ...draft }));
    wrap(<CampaignWizard draftStorageKey="w" direction={direction} />);
    await screen.findByRole("button", { name: /Next: Knowledge/ }, { timeout: SLOW });
    // Other controls on the step are pressed buttons too (the calling days).
    return screen.getAllByRole("button", { pressed: true })
        .map((button) => button.textContent ?? "")
        .filter((text) => PERSONAS.some((persona) => text.startsWith(persona.title)));
}

test("the wizard opens a new inbound campaign on the AI Receptionist persona", async () => {
    const pressed = await wizardPersona("inbound");
    assert.equal(pressed.length, 1);
    assert.match(pressed[0], /^AI Receptionist/);
});

test("the wizard still opens a new outbound campaign on Lead Generation", async () => {
    const pressed = await wizardPersona("outbound");
    assert.equal(pressed.length, 1);
    assert.match(pressed[0], /^Lead Generation/);
});

test("the wizard keeps the persona of a restored inbound draft", async () => {
    const pressed = await wizardPersona("inbound", { personaType: "customer_support" });
    assert.equal(pressed.length, 1);
    assert.match(pressed[0], /^Customer Support/);
});

async function classicPersona(direction: "inbound" | "outbound") {
    wrap(<CampaignForm mode="create" direction={direction} />);
    const chosen = await screen.findByRole("radio", { checked: true }, { timeout: SLOW }) as HTMLInputElement;
    return chosen.closest("label")?.textContent ?? "";
}

test("the detailed form opens a new inbound campaign on the AI Receptionist persona", async () => {
    assert.match(await classicPersona("inbound"), /AI Receptionist/);
});

test("the detailed form still opens a new outbound campaign on Lead Generation", async () => {
    assert.match(await classicPersona("outbound"), /Lead Generation/);
});
