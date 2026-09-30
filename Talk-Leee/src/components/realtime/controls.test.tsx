import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React, { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { aiOptionsApi, type AIProviderConfig, type ProviderListResponse } from "@/lib/ai-options-api";
import { RealtimeControls } from "./ai-options-controls";
import { CampaignVoiceSettings } from "./campaign-settings";
import type { CampaignVoiceSettingsValue } from "./types";

const initial: AIProviderConfig = {
    pipeline_mode: "realtime", realtime_model: "gpt-realtime-2", realtime_voice: "ash",
    llm_provider: "groq", llm_model: "unchanged", llm_temperature: 0.6, llm_max_tokens: 150,
    stt_provider: "deepgram", stt_model: "unchanged", stt_engine: "deepgram_flux", stt_language: "en",
    tts_provider: "cartesia", tts_model: "unchanged", tts_voice_id: "traditional-voice", tts_sample_rate: 16000,
    realtime_settings: { speed: 1, prompt: { persona: "support", goal: "Help callers", instructions: "Keep this", opening_greeting: "Welcome" } },
};
const catalog = { available: true, model: "gpt-realtime-2", voices: [{ id: "ash", name: "Ash", description: "Clear" }, { id: "marin", name: "Marin", description: "Warm" }], turn_detection: ["low", "medium", "high"], noise_reduction: ["near_field", "far_field", "none"] };
const originalConfig = aiOptionsApi.getConfig;
const originalProviders = aiOptionsApi.getProviders;
afterEach(() => { cleanup(); aiOptionsApi.getConfig = originalConfig; aiOptionsApi.getProviders = originalProviders; });

test("Realtime controls keep traditional settings intact and preserve each prompt field", () => {
    let saved = initial;
    function Harness() {
        const [config, setConfig] = useState(initial);
        return <RealtimeControls config={config} catalog={catalog} onChange={(next) => { saved = next; setConfig(next); }} onPreview={() => {}} previewing={false} />;
    }
    render(<Harness />);
    fireEvent.change(screen.getByLabelText("Realtime persona"), { target: { value: "sales" } });
    fireEvent.change(screen.getByLabelText("Realtime instructions"), { target: { value: "Ask about their needs" } });
    fireEvent.change(screen.getByLabelText("Speech speed"), { target: { value: "1.2" } });
    assert.equal(saved.realtime_settings?.prompt?.persona, "sales");
    assert.equal(saved.realtime_settings?.prompt?.opening_greeting, "Welcome");
    assert.equal(saved.realtime_settings?.speed, 1.2);
    assert.equal(saved.tts_voice_id, "traditional-voice");
    assert.equal(saved.tts_sample_rate, 16000);
    assert.equal(saved.llm_model, "unchanged");
    assert.ok(screen.getByLabelText("Maximum response tokens"));
});

test("Unavailable Realtime reports its reason and disables controls", () => {
    render(<RealtimeControls config={initial} catalog={{ ...catalog, available: false, unavailable_reason: "OpenAI is not configured" }} onChange={() => {}} onPreview={() => {}} previewing={false} />);
    assert.match(screen.getByRole("alert").textContent || "", /not configured/);
    assert.equal(screen.getByLabelText("Realtime voice").closest("fieldset")?.disabled, true);
});

test("Campaign inherits Realtime defaults and retains its prompt across engine switches", async () => {
    aiOptionsApi.getConfig = async () => initial;
    aiOptionsApi.getProviders = async () => ({ realtime: catalog } as ProviderListResponse);
    let saved: CampaignVoiceSettingsValue = {};
    function Harness() {
        const [value, setValue] = useState<CampaignVoiceSettingsValue>({});
        return <CampaignVoiceSettings value={value} onChange={(next) => { saved = next; setValue(next); }} />;
    }
    render(<Harness />);
    await waitFor(() => assert.equal(saved.pipeline_mode, "realtime"));
    assert.equal(saved.realtime_prompt?.instructions, "Keep this");
    fireEvent.change(screen.getByLabelText("Realtime goal"), { target: { value: "Campaign-specific goal" } });
    fireEvent.change(screen.getByLabelText("Voice engine"), { target: { value: "cascaded" } });
    assert.equal(screen.queryByLabelText("Realtime goal"), null);
    fireEvent.change(screen.getByLabelText("Voice engine"), { target: { value: "realtime" } });
    assert.equal(saved.realtime_prompt?.goal, "Campaign-specific goal");
    assert.equal(saved.realtime_voice, "ash");
});
