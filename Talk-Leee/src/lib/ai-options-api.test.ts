import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";
import { sharedHttpClient } from "@/lib/api";
import { aiOptionsApi } from "./ai-options-api";
import type { AssemblyAISettings } from "./assemblyai-settings";

afterEach(() => mock.restoreAll());

test("AI Options GET and save response preserve all tuning including explicit null eager mode", async () => {
    const stored = {
        llm_provider: "cerebras", llm_model: "gpt-oss-120b", llm_temperature: 0.6, llm_max_tokens: 90,
        stt_provider: "deepgram", stt_model: "flux-general-en", stt_engine: "deepgram_flux", stt_language: "en",
        tts_provider: "deepgram", tts_model: "aura-2", tts_voice_id: "saved-voice", tts_sample_rate: 16000,
        voice_tuning: { stt_eot_threshold: 0.83, stt_eager_eot_threshold: null, stt_eot_timeout_ms: 900,
            turn_0_min_confidence: 0.6, turn_0_min_alpha_chars: 4 },
    };
    const requests: unknown[] = [];
    mock.method(sharedHttpClient(), "request", async (request: { method?: string; body?: unknown }) => {
        requests.push(request.body);
        return request.method === "POST" ? { config: request.body, latency_warnings: [] } : stored;
    });
    const loaded = await aiOptionsApi.getConfig();
    assert.deepEqual(loaded.voice_tuning, stored.voice_tuning);
    const result = await aiOptionsApi.saveConfig(loaded);
    assert.deepEqual(result.config.voice_tuning, stored.voice_tuning);
    assert.equal(result.config.tts_sample_rate, 16000);
    assert.deepEqual(requests[1], loaded);
});

test("AssemblyAI controls survive GET, POST and reload without losing nullable mode defaults or disabled controls", async () => {
    const settings: AssemblyAISettings = {
        region: "eu", mode: "max_accuracy", min_turn_silence: null, max_turn_silence: 2800,
        interruption_delay: 600, vad_threshold: 0.25, include_partial_turns: null,
        prompt: "English support calls for Acme.", keyterms_prompt: ["Acme"], auto_agent_context: true,
        previous_context_n_turns: 7, language_detection: false, speaker_labels: false,
        max_speakers: 2, speaker_labels_revision_interval_ms: 120000,
        voice_focus: null, voice_focus_threshold: 0.8, domain: "medical-v1",
        redact_pii: false, redact_pii_policies: ["credit_card_number"], redact_pii_sub: "entity_name",
        filter_profanity: true, session_heartbeat: true, inactivity_timeout: 120,
    };
    const stored = {
        llm_provider: "cerebras", llm_model: "gpt-oss-120b", llm_temperature: 0.6, llm_max_tokens: 90,
        stt_provider: "assemblyai", stt_model: "universal-3-6-pro", stt_engine: "assemblyai", stt_language: "en",
        tts_provider: "deepgram", tts_model: "aura-2", tts_voice_id: "saved-voice", tts_sample_rate: 16000,
        assemblyai_settings: settings,
    };
    let posted: unknown;
    mock.method(sharedHttpClient(), "request", async (request: { method?: string; body?: unknown }) => {
        if (request.method === "POST") { posted = request.body; return { config: posted, latency_warnings: [] }; }
        return posted ?? stored;
    });
    const loaded = await aiOptionsApi.getConfig();
    assert.deepEqual(loaded.assemblyai_settings, settings);
    const saved = await aiOptionsApi.saveConfig(loaded);
    assert.deepEqual(saved.config.assemblyai_settings, settings);
    const reloaded = await aiOptionsApi.getConfig();
    assert.deepEqual(reloaded.assemblyai_settings, settings);
    assert.equal(reloaded.stt_language, "en");
});

test("provider availability is preserved so unavailable AssemblyAI cannot masquerade as ready", async () => {
    mock.method(sharedHttpClient(), "request", async () => ({
        llm: { providers: [], models: [] }, tts: { providers: [], models: [] },
        stt: { providers: ["assemblyai"], models: [], engines: [{ id: "assemblyai", name: "AssemblyAI 3.6 Pro", description: "English", available: false, unavailable_reason: "AssemblyAI API key is not configured." }] },
    }));
    const catalog = await aiOptionsApi.getProviders();
    assert.equal(catalog.stt.engines?.[0].available, false);
    assert.equal(catalog.stt.engines?.[0].unavailable_reason, "AssemblyAI API key is not configured.");
});
