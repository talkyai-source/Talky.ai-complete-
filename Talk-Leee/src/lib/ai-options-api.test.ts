import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";
import { sharedHttpClient } from "@/lib/api";
import { aiOptionsApi } from "./ai-options-api";

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
