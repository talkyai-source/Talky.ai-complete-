import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { sharedHttpClient } from "@/lib/api";

// Keep the actual page, query hooks, parser and save handler. Only unrelated
// dashboard navigation, dialogs and verified identity input are replaced; query scoping remains real.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
let identity = { user: { id: "user-a", tenant_id: "tenant-a" }, status: "authenticated", loading: false };
loader._load = function (id, ...args) {
    if (id.includes("hooks/useAuth")) return { useAuth: () => identity };
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/campaigns/apply-to-campaigns-modal")) return { ApplyToCampaignsModal: () => null };
    if (id.includes("components/ai-options/voice-clone-modal")) return { VoiceCloneModal: () => null };
    return originalLoad.call(this, id, ...args);
};
let AIOptionsPage: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    AIOptionsPage = require("./page").default;
} finally {
    loader._load = originalLoad;
}

afterEach(() => { cleanup(); mock.restoreAll(); identity = { user: { id: "user-a", tenant_id: "tenant-a" }, status: "authenticated", loading: false }; });

for (const [voiceAvailable, engine, language, fluxActive] of [
    [true, "deepgram_flux", "en", true], [false, "deepgram_flux", "en", true],
    [true, "deepgram_nova", "es", false], [true, "deepgram_flux", "es", false],
] as const) {
    test(`view and save preserve ${engine}/${language} profile with ${voiceAvailable ? "available" : "missing"} voice`, async () => {
        const stored = {
            llm_provider: "cerebras", llm_model: "gpt-oss-120b", llm_temperature: 0.6, llm_max_tokens: 90,
            stt_provider: "deepgram", stt_model: "flux-general-en", stt_engine: engine, stt_language: language,
            tts_provider: "deepgram", tts_model: voiceAvailable ? "aura-2" : "saved-model", tts_voice_id: "saved-voice", tts_sample_rate: 16000,
            voice_tuning: { stt_eager_eot_threshold: null, stt_eot_timeout_ms: 900 }, pipeline_mode: "cascaded",
        };
        let posted: typeof stored | undefined;
        const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
        mock.method(sharedHttpClient(), "request", async (request: { path: string; method?: string; body?: typeof stored }) => {
            if (request.path.endsWith("/providers")) return {
                llm: { providers: ["cerebras"], models: [{ id: "gpt-oss-120b", name: "Cerebras", description: "Test", provider: "cerebras" }] },
                stt: { providers: ["deepgram"], models: [], engines: [] },
                tts: { providers: ["deepgram"], models: [{ id: "aura-2", name: "Aura 2", description: "Test", provider: "deepgram" }] },
            };
            if (request.path.endsWith("/voices")) return { voices: [{ id: voiceAvailable ? "saved-voice" : "different-voice", name: "Test voice", description: "Synthetic", provider: "deepgram" }] };
            assert.equal(request.path, "/ai-options/config");
            if (request.method === "POST") { posted = request.body; return { config: posted, latency_warnings: [] }; }
            return stored;
        });
        try {
            render(<QueryClientProvider client={qc}><AIOptionsPage /></QueryClientProvider>);
            await waitFor(() => assert.ok(screen.getAllByRole("button", { name: /Save Config/ }).length));
            assert.equal(screen.getByRole("group", { name: "Flux turn detection" }).hasAttribute("disabled"), !fluxActive);
            fireEvent.click(screen.getAllByRole("button", { name: /Save Config/ })[0]);
            await waitFor(() => assert.ok(posted));
            assert.ok(posted);
            assert.equal(posted.tts_voice_id, stored.tts_voice_id);
            assert.equal(posted.tts_model, stored.tts_model);
            assert.equal(posted.tts_sample_rate, stored.tts_sample_rate);
            assert.deepEqual(posted.voice_tuning, stored.voice_tuning);
            if (!voiceAvailable) assert.match(screen.getByRole("status").textContent || "", /saved-voice.*unavailable/i);
        } finally { cleanup(); qc.clear(); }
    });
}

for (const operation of ["save", "preview", "overlapping_preview", "playing_preview"] as const) {
    test(`late ${operation} from account A cannot replace B's draft or play audio`, async () => {
        const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
        let complete!: (value: unknown) => void;
        const pending = new Promise((resolve) => { complete = resolve; });
        let started = false;
        let submitted: Record<string, unknown> | undefined;
        let oldSubmission: Record<string, unknown> | undefined;
        let audioStarts = 0;
        let audioCloses = 0;
        let previewRequests = 0;
        const originalAudio = globalThis.AudioContext;
        globalThis.AudioContext = class {
            constructor() { audioStarts++; }
            createBuffer() { return { getChannelData: () => new Float32Array(1) }; }
            createBufferSource() { return { connect() {}, start() {} }; }
            close() { audioCloses++; return Promise.resolve(); }
        } as unknown as typeof AudioContext;
        mock.method(sharedHttpClient(), "request", async (request: { path: string; method?: string; body?: Record<string, unknown> }) => {
            const account = identity.user.id === "user-a" ? "a" : "b";
            if (request.path.endsWith("/providers")) return {
                llm: { providers: ["cerebras"], models: [] }, stt: { providers: ["deepgram"], models: [] },
                tts: { providers: ["deepgram"], models: [{ id: "aura-2", name: "Aura 2", description: "Test", provider: "deepgram" }] },
            };
            if (request.path.endsWith("/voices")) return { voices: [{ id: `voice-${account}`, name: `Voice ${account.toUpperCase()}`, description: "Synthetic", provider: "deepgram" }] };
            if (request.path.endsWith("/voices/preview")) {
                started = true;
                previewRequests++;
                if ((operation === "overlapping_preview" && previewRequests === 2) || (operation === "playing_preview" && previewRequests === 1)) return { voice_id: "voice-a", audio_base64: "AAAAAA==" };
                return pending;
            }
            assert.equal(request.path, "/ai-options/config");
            if (request.method === "POST") {
                submitted = request.body;
                if (account === "a") { oldSubmission = request.body; started = true; return pending; }
                return { config: submitted, latency_warnings: [] };
            }
            return {
                llm_provider: "cerebras", llm_model: "gpt-oss-120b", llm_temperature: 0.6, llm_max_tokens: 90,
                stt_provider: "deepgram", stt_model: "flux-general-en", stt_engine: "deepgram_flux", stt_language: "en",
                tts_provider: "deepgram", tts_model: "aura-2", tts_voice_id: `voice-${account}`, tts_sample_rate: 16000,
            };
        });
        try {
            const ui = () => <QueryClientProvider client={qc}><AIOptionsPage /></QueryClientProvider>;
            const view = render(ui());
            await waitFor(() => assert.ok(screen.getAllByText("Voice A").length));
            fireEvent.click(operation === "save" ? screen.getAllByRole("button", { name: /Save Config/ })[0] : screen.getByRole("button", { name: "Preview" }));
            await waitFor(() => assert.equal(started, true));
            if (operation === "playing_preview") await waitFor(() => assert.equal(audioStarts, 1));
            if (operation === "overlapping_preview" || operation === "playing_preview") {
                fireEvent.click(screen.getByRole("button", { name: "Preview voice" }));
                await waitFor(() => assert.equal(previewRequests, 2));
                if (operation === "overlapping_preview") await waitFor(() => assert.equal(audioStarts, 1));
                else assert.equal(audioCloses, 1);
            }
            identity = { user: { id: "user-b", tenant_id: "tenant-b" }, status: "authenticated", loading: false };
            view.rerender(ui());
            await waitFor(() => assert.ok(screen.getAllByText("Voice B").length));
            await act(async () => {
                complete(operation === "save" ? { config: oldSubmission, latency_warnings: [] } : { voice_id: "voice-a", audio_base64: "AAAAAA==" });
                await pending;
            });
            assert.equal(audioStarts, operation === "overlapping_preview" || operation === "playing_preview" ? 1 : 0);
            assert.equal(audioCloses, audioStarts);
            assert.equal(screen.queryByText("Voice A"), null);
            fireEvent.click(screen.getAllByRole("button", { name: /Save Config/ })[0]);
            await waitFor(() => assert.equal(submitted?.tts_voice_id, "voice-b"));
        } finally { cleanup(); qc.clear(); globalThis.AudioContext = originalAudio; }
    });
}
