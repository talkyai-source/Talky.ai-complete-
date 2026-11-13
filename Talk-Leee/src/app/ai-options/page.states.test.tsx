/**
 * FX2b Task 6 — AI Options uses the shared states: the page loading is a
 * status and its error keeps its sentence with "Retry"; the voice list loads
 * in the LoadingState and fails in the ErrorState with Retry; save errors are
 * announced (role="alert") and the "saved" banner is a status.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { sharedHttpClient } from "@/lib/api";
import type { AIProviderConfig } from "@/lib/ai-options-api";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id.includes("hooks/useAuth")) return { useAuth: () => ({ user: { id: "user-a", tenant_id: "tenant-a", role: "tenant_admin" }, status: "authenticated", loading: false }) };
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

const SLOW = 15_000;
const originalScrollIntoView = window.Element.prototype.scrollIntoView;
const clients: QueryClient[] = [];
beforeEach(() => {
    mock.method(window.HTMLCanvasElement.prototype, "getContext", () => null);
    window.Element.prototype.scrollIntoView = () => {};
});
afterEach(() => {
    cleanup();
    mock.restoreAll();
    clients.splice(0).forEach((client) => client.clear());
    window.Element.prototype.scrollIntoView = originalScrollIntoView;
});

const stored: AIProviderConfig = {
    llm_provider: "cerebras", llm_model: "gpt-oss-120b", llm_temperature: 0.6, llm_max_tokens: 100,
    stt_provider: "deepgram", stt_model: "nova-3", stt_engine: "deepgram_nova", stt_language: "en",
    tts_provider: "elevenlabs", tts_model: "m", tts_voice_id: "voice", tts_sample_rate: 16000,
};
const providers = {
    llm: { providers: ["cerebras"], models: [] }, tts: { providers: ["elevenlabs"], models: [] },
    stt: { providers: ["deepgram"], models: [], engines: [{ id: "deepgram_nova", name: "Deepgram Nova", description: "Nova" }] },
};
const voiceList = { voices: [{ id: "voice", name: "Listed Voice", description: "Test", provider: "elevenlabs" }] };
type Request = { path: string; method?: string };
type Handlers = {
    providers?: () => Promise<unknown>;
    voices?: () => Promise<unknown>;
    save?: () => Promise<unknown>;
};
const VOICES_SENTENCE = "Voices could not be loaded.";
const shown = (text: string | RegExp) => screen.queryAllByText(text).length > 0;
const status = (title: string) => screen.queryAllByRole("status").some((node) => within(node).queryByText(title) !== null);
const pageShown = () => screen.queryByRole("combobox", { name: "Engine" }) !== null;

function mount(handlers: Handlers = {}) {
    const counts = { providers: 0, voices: 0, save: 0 };
    mock.method(sharedHttpClient(), "request", async (request: Request) => {
        if (request.path.endsWith("/providers")) { counts.providers += 1; return handlers.providers ? handlers.providers() : providers; }
        if (request.path.endsWith("/voices")) { counts.voices += 1; return handlers.voices ? handlers.voices() : voiceList; }
        if (request.method === "POST") { counts.save += 1; return handlers.save ? handlers.save() : { config: stored, latency_warnings: [] }; }
        return stored;
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { gcTime: Infinity } } });
    clients.push(client);
    render(<QueryClientProvider client={client}><AIOptionsPage /></QueryClientProvider>);
    return counts;
}

test("page: loading is a status (the shared LoadingState)", async () => {
    let answer: (() => void) | null = null;
    mount({ providers: () => new Promise((resolve) => { answer = () => resolve(providers); }) });
    await waitFor(() => assert.equal(status("Loading"), true), { timeout: SLOW });
    assert.equal(document.querySelector(".animate-spin") === null, true, "not the page's own spinner");
    await waitFor(() => assert.equal(answer !== null, true), { timeout: SLOW });
    await act(async () => { answer!(); });
    await waitFor(() => assert.equal(pageShown(), true), { timeout: SLOW });
});

test("page: a failed read shows its own sentence in the shared ErrorState with Retry, which reads again", async () => {
    let fail = true;
    const counts = mount({ providers: async () => { if (fail) throw new Error("providers down"); return providers; } });
    const sentence = await screen.findByText("AI options failed to load from the backend.", undefined, { timeout: SLOW });
    assert.equal(sentence.getAttribute("role"), "alert");
    assert.equal(shown("Something went wrong"), true, "the shared ErrorState");
    fail = false;
    const before = counts.providers;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => assert.equal(pageShown(), true), { timeout: SLOW });
    assert.equal(counts.providers, before + 1);
});

test("voices: while the list loads the voice grid shows a status", async () => {
    let answer: (() => void) | null = null;
    mount({ voices: () => new Promise((resolve) => { answer = () => resolve(voiceList); }) });
    await waitFor(() => assert.equal(pageShown(), true), { timeout: SLOW });
    assert.equal(status("Loading"), true);
    assert.equal(shown(/No voices available/), false, "loading is never shown as an empty voice list");
    await act(async () => { answer!(); });
    await waitFor(() => assert.equal(shown("Listed Voice"), true), { timeout: SLOW });
    assert.equal(status("Loading"), false);
});

test("voices: a failed list shows its sentence in the shared ErrorState with Retry, which reads again", async () => {
    let fail = true;
    const counts = mount({ voices: async () => { if (fail) throw new Error("voices unavailable"); return voiceList; } });
    await waitFor(() => assert.equal(pageShown(), true), { timeout: SLOW });
    const sentence = await screen.findByText(VOICES_SENTENCE, undefined, { timeout: SLOW });
    assert.equal(sentence.getAttribute("role"), "alert");
    assert.equal(shown(/Saved voice .* is currently unavailable in the catalog/), false, "FX1b: a failed list does not blame the saved voice");
    fail = false;
    const before = counts.voices;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => assert.equal(shown("Listed Voice"), true), { timeout: SLOW });
    assert.equal(counts.voices, before + 1);
    assert.equal(shown(VOICES_SENTENCE), false);
});

test("save: a failed save is announced (role=alert) with its unchanged text", async () => {
    mount({ save: async () => { throw new Error("Save was refused."); } });
    await waitFor(() => assert.equal(pageShown(), true), { timeout: SLOW });
    fireEvent.click(screen.getAllByRole("button", { name: "Save Configuration" })[0]);
    const text = await screen.findByText("Save was refused.", undefined, { timeout: SLOW });
    assert.equal(text.closest('[role="alert"]') !== null, true);
});

test("save: the 'saved' banner is announced (role=status) with its unchanged text", async () => {
    mount();
    await waitFor(() => assert.equal(pageShown(), true), { timeout: SLOW });
    fireEvent.click(screen.getAllByRole("button", { name: "Save Configuration" })[0]);
    const text = await screen.findByText("Configuration saved successfully!", undefined, { timeout: SLOW });
    assert.equal(text.closest('[role="status"]') !== null, true);
});
