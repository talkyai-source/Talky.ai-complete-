import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { VoiceProviderPicker } from "@/components/campaigns/voice-provider-picker";
import { aiOptionsApi, type AIProviderConfig, type VoiceInfo } from "@/lib/ai-options-api";

const originalVoices = aiOptionsApi.getVoices;
const originalConfig = aiOptionsApi.getConfig;
const originalPreview = aiOptionsApi.previewVoice;

afterEach(() => {
    cleanup();
    aiOptionsApi.getVoices = originalVoices;
    aiOptionsApi.getConfig = originalConfig;
    aiOptionsApi.previewVoice = originalPreview;
});

test("voice catalog failure stays distinct from empty and can recover without previewing audio", async () => {
    let attempts = 0;
    let previews = 0;
    aiOptionsApi.getVoices = async () => {
        attempts += 1;
        if (attempts === 1) throw new Error("Catalog temporarily unavailable");
        const voice: VoiceInfo = {
            id: "test-voice", name: "Fixture", provider: "cartesia", language: "en", tags: [],
            description: "Test voice", accent_color: "blue", preview_text: "Hello",
        };
        return { voices: [voice] };
    };
    aiOptionsApi.getConfig = async () => ({ tts_provider: "cartesia" }) as AIProviderConfig;
    aiOptionsApi.previewVoice = async () => { previews += 1; throw new Error("Unexpected audio request"); };
    render(<VoiceProviderPicker personaType="lead_gen" voiceId="" onVoiceChange={() => {}} />);
    await screen.findByText(/Voices could not be loaded/);
    assert.equal(screen.queryByText("No voices for this provider."), null);
    fireEvent.click(screen.getByRole("button", { name: "Retry voices" }));
    await screen.findByRole("radio", { name: /Select Fixture/ });
    assert.equal(screen.queryByRole("alert"), null);
    assert.equal(attempts, 2);
    assert.equal(previews, 0);
});

test("a successful empty voice catalog is shown as empty", async () => {
    aiOptionsApi.getVoices = async () => ({ voices: [] });
    aiOptionsApi.getConfig = async () => ({ tts_provider: "cartesia" }) as AIProviderConfig;
    render(<VoiceProviderPicker personaType="lead_gen" voiceId="" onVoiceChange={() => {}} />);
    await screen.findByText("No voices for this provider.");
    assert.equal(screen.queryByRole("button", { name: "Retry voices" }), null);
});
