import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import { CallingScheduleEditor } from "@/components/campaigns/calling-schedule-editor";
import { CampaignBasicsEditor } from "@/components/campaigns/campaign-basics-editor";
import { CampaignForm, type CampaignFormInitial } from "@/components/campaigns/campaign-form";
import { CampaignWizard } from "@/components/campaigns/campaign-wizard";
import { aiOptionsApi } from "@/lib/ai-options-api";
import { callingHoursInverted } from "@/lib/calling-window";
import { dashboardApi } from "@/lib/dashboard-api";
import { leadDetailsApi } from "@/lib/lead-details-api";

// Forms audit B-F6: the server refuses start > end (equal is accepted), so the
// forms must say so as soon as it happens and block Next / Save.
const SLOW = 15_000;
const REALTIME = { pipeline_mode: "realtime" as const, realtime_model: "gpt-realtime", realtime_voice: "alloy" };
const INVERTED = { timezone: "Europe/London", time_window_start: "19:00", time_window_end: "09:00", allowed_days: [0] };
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

test("callingHoursInverted mirrors the server rule: only start > end", () => {
    assert.equal(callingHoursInverted({ time_window_start: "19:00", time_window_end: "09:00" }), true);
    assert.equal(callingHoursInverted({ time_window_start: "09:00", time_window_end: "09:00" }), false);
    assert.equal(callingHoursInverted({ time_window_start: "09:00", time_window_end: "19:00" }), false);
    assert.equal(callingHoursInverted({ time_window_start: "09:00" }), false);
    assert.equal(callingHoursInverted({}), false);
});

test("the schedule editor shows an inline error as soon as the end is before the start", () => {
    const { rerender } = render(<CallingScheduleEditor value={{ time_window_start: "09:00", time_window_end: "17:00" }} onChange={() => {}} />);
    assert.equal(screen.queryByTestId("calling-hours-order-error"), null);
    rerender(<CallingScheduleEditor value={INVERTED} onChange={() => {}} />);
    assert.match(screen.getByTestId("calling-hours-order-error").textContent ?? "", /must start before they end/);
});

test("the wizard's Next is disabled while the calling hours are inverted", async () => {
    window.sessionStorage.setItem("w", JSON.stringify({
        step: 0, voiceSelection: REALTIME, name: "Spring", companyName: "Acme", agentNamesRaw: "Alex", personaType: "lead_gen", schedule: INVERTED,
    }));
    wrap(<CampaignWizard draftStorageKey="w" />);
    const next = await screen.findByRole("button", { name: /Next: Knowledge/ }, { timeout: SLOW }) as HTMLButtonElement;
    await screen.findByTestId("calling-hours-order-error");
    assert.equal(next.disabled, true);
    await act(async () => { fireEvent.change(screen.getByLabelText("Calling end time"), { target: { value: "20:00" } }); });
    await waitFor(() => assert.equal(next.disabled, false), { timeout: SLOW });
});

test("the basics editor blocks Save while the calling hours are inverted", async () => {
    wrap(<CampaignBasicsEditor campaignId="c1" initial={{
        voiceSelection: REALTIME, name: "Spring", companyName: "Acme", personaType: "lead_gen", agentNames: ["Alex"], voiceId: "", goal: "",
        callingSchedule: INVERTED,
    }} />);
    const save = await screen.findByRole("button", { name: "Save changes" }, { timeout: SLOW }) as HTMLButtonElement;
    await screen.findByTestId("calling-hours-order-error");
    assert.equal(save.disabled, true);
});

test("the classic form refuses to save inverted hours and says why", async () => {
    const update = mock.method(dashboardApi, "updateCampaign", async () => { throw new Error("must not be called"); });
    const initial: CampaignFormInitial = {
        voiceSelection: REALTIME, name: "Form campaign", description: "", system_prompt: "", voice_id: "", goal: "",
        persona_type: "lead_gen", company_name: "Acme", agent_names: ["Alex"], slots: {}, calling_schedule: INVERTED,
    };
    const { container } = wrap(<CampaignForm mode="edit" campaignId="c1" initialData={initial} />);
    const button = await screen.findByRole("button", { name: "Save campaign" }, { timeout: SLOW }) as HTMLButtonElement;
    await waitFor(() => assert.equal(button.disabled, false), { timeout: SLOW });
    await act(async () => { fireEvent.submit(container.querySelector("form")!); });
    await waitFor(() => assert.ok(screen.getAllByText(/must start before they end/).length >= 1), { timeout: SLOW });
    assert.equal(update.mock.callCount(), 0);
});
