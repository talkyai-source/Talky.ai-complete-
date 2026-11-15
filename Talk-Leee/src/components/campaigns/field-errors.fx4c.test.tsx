/**
 * FX4c Task A2 — campaign edit and the calling schedule: a field error has an
 * id, the field names it in aria-describedby and carries aria-invalid="true"
 * while in error. No error text is new.
 */
import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import { CallingScheduleEditor } from "@/components/campaigns/calling-schedule-editor";
import { CampaignBasicsEditor } from "@/components/campaigns/campaign-basics-editor";
import { aiOptionsApi } from "@/lib/ai-options-api";
import { leadDetailsApi } from "@/lib/lead-details-api";

const SLOW = 15_000;
const REALTIME = { pipeline_mode: "realtime" as const, realtime_model: "gpt-realtime", realtime_voice: "alloy" };
const router = { push() {}, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } as unknown as React.ContextType<typeof AppRouterContext>;
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

const invalid = (field: Element) => field.getAttribute("aria-invalid") === "true";
const describedText = (field: Element) => (field.getAttribute("aria-describedby") ?? "").split(" ").filter(Boolean)
    .map((id) => document.getElementById(id)?.textContent?.trim() ?? null);

test("calling schedule: the hours-order error is tied to both time fields, and only while it shows", () => {
    const { rerender } = render(<CallingScheduleEditor value={{ timezone: "Europe/London", time_window_start: "09:00", time_window_end: "17:00", allowed_days: [0] }} onChange={() => {}} />);
    const start = () => screen.getByLabelText("Calling start time");
    const end = () => screen.getByLabelText("Calling end time");
    assert.equal(invalid(start()) || invalid(end()), false);
    assert.equal(start().hasAttribute("aria-describedby") || end().hasAttribute("aria-describedby"), false);

    rerender(<CallingScheduleEditor value={{ timezone: "Europe/London", time_window_start: "18:00", time_window_end: "09:00", allowed_days: [0] }} onChange={() => {}} />);
    const error = screen.getByTestId("calling-hours-order-error");
    assert.equal(Boolean(error.id), true, "the error has an id");
    assert.equal(invalid(start()) && invalid(end()), true);
    assert.deepEqual(describedText(start()), [error.textContent?.trim()]);
    assert.deepEqual(describedText(end()), [error.textContent?.trim()]);
});

test("campaign edit: the representative-name and guidance errors are tied to their fields", async () => {
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
    await screen.findByRole("button", { name: "Save changes" }, { timeout: SLOW });
    const names = () => document.getElementById("ce-agents") as HTMLInputElement;
    const goal = () => document.getElementById("ce-goal") as HTMLTextAreaElement;
    assert.equal(invalid(names()) || names().hasAttribute("aria-describedby"), false);
    assert.equal(invalid(goal()) || goal().hasAttribute("aria-describedby"), false);

    fireEvent.change(names(), { target: { value: "A".repeat(121) } });
    assert.equal(invalid(names()), true);
    assert.deepEqual(describedText(names()), ["The first representative name must be 120 characters or fewer."]);
    fireEvent.change(names(), { target: { value: "Alex" } });
    assert.equal(invalid(names()) || names().hasAttribute("aria-describedby"), false);

    fireEvent.change(goal(), { target: { value: "x".repeat(60_000) } });
    assert.equal(invalid(goal()), true, "the guidance is over its budget");
    const described = describedText(goal());
    assert.equal(described.length === 1 && typeof described[0] === "string" && described[0].length > 0, true);
    assert.equal(document.getElementById("ce-goal-error")?.className.includes("text-destructive"), true);
});
