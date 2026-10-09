import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { sharedHttpClient } from "@/lib/api";
import type { AIProviderConfig } from "@/lib/ai-options-api";

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

// Exercise the real Select while avoiding unsupported JSDOM canvas drawing.
beforeEach(() => mock.method(window.HTMLCanvasElement.prototype, "getContext", () => null));

for (const [label, mode] of [["Balanced", "balanced"], ["Fast", "min_latency"], ["Accuracy", "max_accuracy"]] as const) {
    test(`AssemblyAI ${label} is selectable, English-only, retained through Flux switching and saved/reloaded`, async () => {
        let stored: AIProviderConfig = {
            llm_provider: "cerebras", llm_model: "gpt-oss-120b", llm_temperature: 0.6, llm_max_tokens: 90,
            stt_provider: "deepgram", stt_model: "nova-3", stt_engine: "deepgram_nova", stt_language: "es",
            tts_provider: "deepgram", tts_model: "aura-2", tts_voice_id: "voice", tts_sample_rate: 16000,
            voice_tuning: { stt_eot_timeout_ms: 900 },
        };
        let posted: AIProviderConfig | undefined;
        const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
        mock.method(sharedHttpClient(), "request", async (request: { path: string; method?: string; body?: AIProviderConfig }) => {
            if (request.path.endsWith("/providers")) return {
                llm: { providers: ["cerebras"], models: [] }, tts: { providers: ["deepgram"], models: [] },
                stt: { providers: ["deepgram", "assemblyai"], models: [], engines: [
                    { id: "deepgram_flux", name: "Deepgram Flux", description: "Flux" },
                    { id: "deepgram_nova", name: "Deepgram Nova", description: "Nova" },
                    { id: "assemblyai", name: "AssemblyAI 3.6 Pro", description: "English streaming", available: true },
                ] },
            };
            if (request.path.endsWith("/voices")) return { voices: [{ id: "voice", name: "Saved Voice", description: "Test", provider: "deepgram" }] };
            if (request.method === "POST") { posted = request.body; stored = posted!; return { config: stored, latency_warnings: [] }; }
            return stored;
        });
        const select = async (name: string, option: string) => {
            fireEvent.click(screen.getByRole("combobox", { name }));
            fireEvent.click(await screen.findByRole("option", { name: option }));
        };
        const renderPage = () => render(<QueryClientProvider client={qc}><AIOptionsPage /></QueryClientProvider>);
        try {
            let view = renderPage();
            await waitFor(() => assert.ok(screen.getByRole("combobox", { name: "Engine" })));
            await select("Engine", "AssemblyAI 3.6 Pro");
            const english = screen.getByLabelText("Transcription language") as HTMLInputElement;
            assert.equal(english.value, "English (en)");
            assert.equal(english.disabled, true);
            assert.equal(screen.getByRole("group", { name: "Flux turn detection" }).hasAttribute("disabled"), true);
            await select("AssemblyAI mode", label);
            fireEvent.change(screen.getByLabelText("Audio context prompt"), { target: { value: "English appointment calls." } });
            fireEvent.change(screen.getByLabelText("Maximum turn silence (ms)"), { target: { value: "2800" } });
            await select("Engine", "Deepgram Flux");
            assert.equal(screen.queryByLabelText("Transcription language"), null);
            await select("Engine", "AssemblyAI 3.6 Pro");
            assert.match(screen.getByRole("combobox", { name: "AssemblyAI mode" }).textContent || "", new RegExp(label));
            fireEvent.click(screen.getAllByRole("button", { name: /Save Config/ })[0]);
            await waitFor(() => assert.ok(posted));
            assert.equal(posted?.stt_engine, "assemblyai");
            assert.equal(posted?.stt_provider, "assemblyai");
            assert.equal(posted?.stt_model, "universal-3-6-pro");
            assert.equal(posted?.stt_language, "en");
            assert.equal(posted?.assemblyai_settings?.mode, mode);
            assert.equal(posted?.assemblyai_settings?.max_turn_silence, 2800);
            assert.equal(posted?.assemblyai_settings?.min_turn_silence, undefined);
            assert.deepEqual(posted?.voice_tuning, { stt_eot_timeout_ms: 900 });
            view.unmount(); qc.clear();
            view = renderPage();
            await waitFor(() => assert.ok(screen.getByRole("combobox", { name: "AssemblyAI mode" })));
            assert.match(screen.getByRole("combobox", { name: "AssemblyAI mode" }).textContent || "", new RegExp(label));
            assert.equal((screen.getByLabelText("Audio context prompt") as HTMLTextAreaElement).value, "English appointment calls.");
            assert.equal((screen.getByLabelText("Maximum turn silence (ms)") as HTMLInputElement).value, "2800");
            view.unmount();
        } finally { cleanup(); qc.clear(); }
    });
}

afterEach(() => { cleanup(); mock.restoreAll(); identity = { user: { id: "user-a", tenant_id: "tenant-a" }, status: "authenticated", loading: false }; });

for (const savedEngine of ["deepgram_flux", "assemblyai"] as const) {
    test(`missing AssemblyAI credentials are visible and cannot be selected or saved from ${savedEngine}`, async () => {
        const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
        let saves = 0;
        const reason = "AssemblyAI API key is not configured.";
        mock.method(sharedHttpClient(), "request", async (request: { path: string; method?: string; body?: AIProviderConfig }) => {
            if (request.path.endsWith("/providers")) return {
                llm: { providers: ["cerebras"], models: [] }, tts: { providers: ["deepgram"], models: [] },
                stt: { providers: ["deepgram", "assemblyai"], models: [], engines: [
                    { id: "deepgram_flux", name: "Deepgram Flux", description: "Flux", available: true },
                    { id: "assemblyai", name: "AssemblyAI 3.6 Pro", description: "English streaming", available: false, unavailable_reason: reason },
                ] },
            };
            if (request.path.endsWith("/voices")) return { voices: [{ id: "voice", name: "Saved Voice", description: "Test", provider: "deepgram" }] };
            if (request.method === "POST") { saves++; return { config: request.body, latency_warnings: [] }; }
            return {
                llm_provider: "cerebras", llm_model: "gpt-oss-120b", llm_temperature: 0.6, llm_max_tokens: 90,
                stt_provider: savedEngine === "assemblyai" ? "assemblyai" : "deepgram",
                stt_model: savedEngine === "assemblyai" ? "universal-3-6-pro" : "flux-general-en", stt_engine: savedEngine, stt_language: "en",
                tts_provider: "deepgram", tts_model: "aura-2", tts_voice_id: "voice", tts_sample_rate: 16000,
            };
        });
        try {
            render(<QueryClientProvider client={qc}><AIOptionsPage /></QueryClientProvider>);
            await waitFor(() => assert.ok(screen.getByRole("combobox", { name: "Engine" })));
            assert.ok(screen.getByText(new RegExp(reason.replaceAll(".", "\\."))));
            fireEvent.click(screen.getByRole("combobox", { name: "Engine" }));
            const unavailable = await screen.findByRole("option", { name: "AssemblyAI 3.6 Pro (unavailable)" });
            assert.equal((unavailable as HTMLButtonElement).disabled, true);
            fireEvent.click(unavailable);
            if (savedEngine === "deepgram_flux") {
                assert.match(screen.getByRole("combobox", { name: "Engine" }).textContent || "", /Deepgram Flux/);
            }
            fireEvent.keyDown(screen.getByRole("combobox", { name: "Engine" }), { key: "Escape" });
            fireEvent.click(screen.getAllByRole("button", { name: /Save Config/ })[0]);
            if (savedEngine === "assemblyai") {
                await waitFor(() => assert.ok(screen.getAllByText(reason).length >= 2));
                assert.equal(saves, 0);
            } else {
                await waitFor(() => assert.equal(saves, 1));
            }
        } finally { cleanup(); qc.clear(); }
    });
}

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
