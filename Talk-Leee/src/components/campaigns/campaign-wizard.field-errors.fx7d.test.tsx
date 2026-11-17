/**
 * FX7d Task 2 — the campaign-create wizard: a field error has an id, and the
 * field names it in aria-describedby (and carries aria-invalid="true" where
 * the control may) while the error shows. No error text is new.
 */
import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import { CampaignWizard } from "@/components/campaigns/campaign-wizard";
import { aiOptionsApi } from "@/lib/ai-options-api";
import { dashboardApi } from "@/lib/dashboard-api";
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
    return render(
        <AppRouterContext.Provider value={router}>
            <QueryClientProvider client={client}><CampaignWizard draftStorageKey="w" /></QueryClientProvider>
        </AppRouterContext.Provider>,
    );
}

const invalid = (field: Element) => field.getAttribute("aria-invalid") === "true";
const describedText = (field: Element) => (field.getAttribute("aria-describedby") ?? "").split(" ").filter(Boolean)
    .map((id) => document.getElementById(id)?.textContent?.trim() ?? null);

test("representative names: the 120-character error is tied to the field, and only while it shows", async () => {
    mount({ agentNamesRaw: "Alex" });
    await screen.findByRole("button", { name: /Next: Knowledge/ }, { timeout: SLOW });
    const names = () => document.getElementById("cw-agents") as HTMLInputElement;
    assert.equal(invalid(names()) || names().hasAttribute("aria-describedby"), false);

    await act(async () => { fireEvent.change(names(), { target: { value: "A".repeat(121) } }); });
    assert.equal(invalid(names()), true);
    assert.deepEqual(describedText(names()), ["The first representative name must be 120 characters or fewer."]);

    await act(async () => { fireEvent.change(names(), { target: { value: "Alex" } }); });
    assert.equal(invalid(names()) || names().hasAttribute("aria-describedby"), false);
});

test("additional campaign guidance: the over-budget message is tied to the field, and the usual hint is not", async () => {
    mount({});
    await screen.findByRole("button", { name: /Next: Knowledge/ }, { timeout: SLOW });
    const goal = () => document.getElementById("cw-goal") as HTMLTextAreaElement;
    assert.equal(invalid(goal()) || goal().hasAttribute("aria-describedby"), false);
    assert.equal(document.getElementById("cw-goal-error") === null, true, "the hint is not an error");

    await act(async () => { fireEvent.change(goal(), { target: { value: "x".repeat(60_000) } }); });
    assert.equal(invalid(goal()), true);
    const described = describedText(goal());
    assert.equal(described.length === 1 && typeof described[0] === "string" && /^Campaign guidance is [\d,]+ characters; the limit is/.test(described[0]), true);
    assert.equal(document.getElementById("cw-goal-error")?.className.includes("text-destructive"), true);

    await act(async () => { fireEvent.change(goal(), { target: { value: "" } }); });
    assert.equal(invalid(goal()) || goal().hasAttribute("aria-describedby"), false);
});

test("knowledge file: a refused file's message is tied to the file button, by description (a button takes no aria-invalid)", async () => {
    const { container } = mount({ step: 1, name: "Spring", companyName: "Acme", agentNamesRaw: "Alex" });
    const chooser = () => screen.getByRole("button", { name: /Choose a \.md or \.txt file/ });
    await screen.findByRole("button", { name: /Next: Review/ }, { timeout: SLOW });
    assert.equal(chooser().hasAttribute("aria-describedby"), false);
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;

    await act(async () => { fireEvent.change(input, { target: { files: [new File(["x"], "notes.pdf", { type: "application/pdf" })] } }); });
    assert.deepEqual(describedText(chooser()), ["Please choose a .md or .txt file."]);
    assert.equal(document.getElementById("cw-error")?.getAttribute("role"), "alert");
    assert.equal(chooser().hasAttribute("aria-invalid"), false);

    await act(async () => { fireEvent.change(input, { target: { files: [new File([new Uint8Array(10 * 1024 * 1024 + 1)], "big.md")] } }); });
    assert.deepEqual(describedText(chooser()), ["File too large (max 10.0 MB)."]);

    // Another message in the same banner is not about the file: going back and forward clears it.
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Back/ })); });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Next: Knowledge/ })); });
    assert.equal(document.getElementById("cw-error") === null, true);
    assert.equal(chooser().hasAttribute("aria-describedby"), false);
});
