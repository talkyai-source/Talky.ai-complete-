import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import { CampaignWizard } from "@/components/campaigns/campaign-wizard";
import { aiOptionsApi } from "@/lib/ai-options-api";
import { dashboardApi } from "@/lib/dashboard-api";
import { leadDetailsApi } from "@/lib/lead-details-api";

// Live test, 14 Nov 2026: typing that arrives faster than the page redraws
// lost about one character in 53 on the create-campaign wizard. The events
// below arrive back to back, with no pause in which React could finish its
// follow-up work, which is what a fast run of keys does on a slow page.
// The cause was the shared Select (see select.rerender.test.tsx): the wizard
// shows several, and each one scheduled a render of its own after every key.
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
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    cleanup();
    clients.splice(0).forEach((client) => client.clear());
    window.sessionStorage.clear();
    mock.restoreAll();
});

function mount() {
    window.sessionStorage.setItem("w", JSON.stringify({ step: 0, voiceSelection: REALTIME, personaType: "lead_gen" }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.push(client);
    render(
        <AppRouterContext.Provider value={router}>
            <QueryClientProvider client={client}><CampaignWizard draftStorageKey="w" /></QueryClientProvider>
        </AppRouterContext.Provider>,
    );
}

/** One key press as the browser delivers it: the field's value grows by one character. */
function press(field: HTMLInputElement | HTMLTextAreaElement, text: string) {
    const proto = field instanceof window.HTMLTextAreaElement ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(field, text);
    field.dispatchEvent(new window.Event("input", { bubbles: true }));
}

for (const id of ["cw-name", "cw-goal"]) {
    test(`a fast run of 120 key presses loses no character: ${id}`, async () => {
        mount();
        await screen.findByRole("button", { name: /Next: Knowledge/ }, { timeout: SLOW });
        const field = document.getElementById(id) as HTMLInputElement | HTMLTextAreaElement;
        field.removeAttribute("maxlength");
        const errors: unknown[] = [];
        const onError = (event: ErrorEvent) => { errors.push(event.error ?? event.message); event.preventDefault(); };
        window.addEventListener("error", onError);
        // Real browser input is not wrapped in act(): React must cope by itself.
        (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = false;
        const full = Array.from({ length: 120 }, (_, i) => "abcdefghij"[i % 10]).join("");
        try {
            for (let length = 1; length <= full.length; length += 1) press(field, full.slice(0, length));
        } finally {
            window.removeEventListener("error", onError);
        }
        assert.deepEqual(errors.map(String), [], "no error while typing");
        assert.equal(field.value.length, full.length, "every character is in the field");
        assert.equal(field.value, full);
    });
}
