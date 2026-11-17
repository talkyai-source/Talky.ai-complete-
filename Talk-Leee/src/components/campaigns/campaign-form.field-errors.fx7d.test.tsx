/**
 * FX7d Task 2 — the shared campaign form: a message about one field has an
 * id, and that field names it in aria-describedby and carries
 * aria-invalid="true" for as long as the message shows. A server refusal stays
 * a form-level message. No error text is new.
 */
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
const router = { push() {}, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } as unknown as React.ContextType<typeof AppRouterContext>;
const clients: QueryClient[] = [];
const VOICE = { id: "voice-1", name: "Harper", language: "en", provider: "cartesia", gender: "female" };

beforeEach(() => {
    mock.method(aiOptionsApi, "getProviders", async () => ({
        realtime: { available: true, model: "gpt-realtime", voices: [{ id: "alloy", name: "Alloy" }] },
    }));
    mock.method(aiOptionsApi, "getConfig", async () => ({ pipeline_mode: "cascaded", tts_provider: "cartesia", tts_model: "sonic" }));
    mock.method(aiOptionsApi, "getVoices", async () => ({ voices: [VOICE] }));
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

const REALTIME: CampaignFormInitial = {
    voiceSelection: { pipeline_mode: "realtime", realtime_model: "gpt-realtime", realtime_voice: "alloy" },
    name: "Form campaign", description: "", system_prompt: "", voice_id: "", goal: "",
    persona_type: "lead_gen", company_name: "Acme", agent_names: ["Alex"], slots: {},
};
const CASCADED: CampaignFormInitial = {
    ...REALTIME, voiceSelection: { pipeline_mode: "cascaded" }, voice_id: VOICE.id,
    system_prompt: "Call facilities managers and book a short assessment visit with them.",
};

async function open(initial: CampaignFormInitial) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.push(client);
    const { container } = render(
        <AppRouterContext.Provider value={router}>
            <QueryClientProvider client={client}>
                <CampaignForm mode="edit" campaignId="campaign-1" initialData={initial} />
            </QueryClientProvider>
        </AppRouterContext.Provider>,
    );
    await screen.findByRole("button", { name: "Save campaign" }, { timeout: SLOW });
    await waitFor(() => assert.equal(screen.queryByText("Loading contact fields…") === null, true), { timeout: SLOW });
    return { submit: async () => { await act(async () => { fireEvent.submit(container.querySelector("form")!); }); } };
}

const field = (id: string) => document.getElementById(id) as HTMLElement;
const invalid = (el: Element) => el.getAttribute("aria-invalid") === "true";
const describedText = (el: Element) => (el.getAttribute("aria-describedby") ?? "").split(" ").filter(Boolean)
    .map((id) => document.getElementById(id)?.textContent?.trim() ?? null);
const untied = (id: string) => !invalid(field(id)) && !field(id).hasAttribute("aria-describedby");

test("campaign name: 'Add a campaign name.' is tied to the name field only", async () => {
    const { submit } = await open({ ...REALTIME, name: "   " });
    assert.equal(untied("name"), true);
    await submit();
    assert.equal(invalid(field("name")), true);
    assert.deepEqual(describedText(field("name")), ["Add a campaign name."]);
    assert.equal(field("campaign-form-error").getAttribute("role"), "alert");
    assert.equal(untied("company_name") && untied("agent_names"), true);
});

test("representative names: 'Add at least one agent name' is tied to the names field", async () => {
    const { submit } = await open({ ...REALTIME, agent_names: [] });
    await submit();
    assert.equal(invalid(field("agent_names")), true);
    assert.deepEqual(describedText(field("agent_names")), ["Add at least one agent name (up to three — we rotate per call)."]);
    assert.equal(untied("name") && untied("company_name"), true);
});

test("representative names: the 120-character message is tied to the names field", async () => {
    const { submit } = await open({ ...REALTIME, agent_names: ["A".repeat(121)] });
    await submit();
    assert.equal(invalid(field("agent_names")), true);
    assert.deepEqual(describedText(field("agent_names")), ["The first representative name must be 120 characters or fewer."]);
});

test("brand / company name: 'Add the company or business name' is tied to the company field", async () => {
    const { submit } = await open({ ...REALTIME, company_name: "  " });
    await submit();
    assert.equal(invalid(field("company_name")), true);
    assert.deepEqual(describedText(field("company_name")), ["Add the company or business name your agent represents."]);
    assert.equal(untied("name") && untied("agent_names"), true);
});

test("a server refusal replaces the message and is tied to no field", async () => {
    const update = mock.method(dashboardApi, "updateCampaign", async () => { throw new Error("Campaign name is already in use."); });
    const { submit } = await open({ ...REALTIME, name: "   " });
    await submit();
    assert.equal(invalid(field("name")), true);
    await act(async () => { fireEvent.change(field("name"), { target: { name: "name", value: "Spring" } }); });
    await submit();
    await waitFor(() => assert.equal(field("campaign-form-error")?.textContent?.trim(), "Campaign name is already in use."), { timeout: SLOW });
    assert.equal(update.mock.callCount(), 1);
    assert.equal(untied("name") && untied("company_name") && untied("agent_names"), true);
});

test("campaign guidance: the over-limit message is tied to the field, and only while the guidance is not valid", async () => {
    await open(CASCADED);
    const guidance = () => field("system_prompt");
    assert.equal(untied("system_prompt"), true);
    assert.equal(document.getElementById("system_prompt-error") === null, true);

    await act(async () => { fireEvent.change(guidance(), { target: { name: "system_prompt", value: "x".repeat(60_000) } }); });
    assert.equal(invalid(guidance()), true);
    const described = describedText(guidance());
    assert.equal(described.length === 1 && typeof described[0] === "string" && /^Campaign guidance is [\d,]+ characters; the limit is/.test(described[0]), true);

    await act(async () => { fireEvent.change(guidance(), { target: { name: "system_prompt", value: CASCADED.system_prompt } }); });
    assert.equal(untied("system_prompt"), true);
    assert.equal(document.getElementById("system_prompt-error") === null, true);
});

test("persona details: 'Missing required fields' is tied to each missing field and to no other", async () => {
    const { submit } = await open({ ...CASCADED, slots: { industry: "roofing" } });
    await act(async () => {
        fireEvent.change(field("campaign-brief-opening-objective"), { target: { value: "Confirm who owns vendor selection." } });
    });
    await submit();
    const message = field("campaign-form-error")?.textContent?.trim() ?? "";
    assert.equal(message.startsWith("Missing required fields: Services, Coverage area"), true, message);
    for (const key of ["services_description", "coverage_area", "value_proposition", "call_reason"]) {
        assert.equal(invalid(field(`slot-${key}`)), true, key);
        assert.deepEqual(describedText(field(`slot-${key}`)), [message], key);
    }
    assert.equal(untied("slot-industry"), true, "a filled required field");
    assert.equal(untied("slot-pricing_info"), true, "an optional field");
    assert.equal(untied("name"), true);
});

test("prompt preview: its button is off without a company name, so 'Add a company name before previewing.' cannot show", async () => {
    await open({ ...CASCADED, company_name: "" });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Show preview" })); });
    assert.equal((screen.getByRole("button", { name: "Render preview" }) as HTMLButtonElement).disabled, true);
    assert.equal(untied("company_name"), true);
});
