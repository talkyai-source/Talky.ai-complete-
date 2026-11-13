/**
 * FX3b Task 5.4 — the voice list says "Voices could not be loaded." for a
 * failure that is not a timeout; a timeout keeps "Voices took too long to
 * load. Reload to fetch them.". Same boundary stubs as page.states.test.tsx.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { sharedHttpClient } from "@/lib/api";
import { ApiClientError } from "@/lib/http-client";
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
const TIMEOUT_SENTENCE = "Voices took too long to load. Reload to fetch them.";
const FAILED_SENTENCE = "Voices could not be loaded.";
type Request = { path: string; method?: string };
const shown = (text: string) => screen.queryAllByText(text).length > 0;

function mount(voices: () => Promise<unknown>) {
    mock.method(sharedHttpClient(), "request", async (request: Request) => {
        if (request.path.endsWith("/providers")) return providers;
        if (request.path.endsWith("/voices")) return voices();
        return stored;
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { gcTime: Infinity } } });
    clients.push(client);
    render(<QueryClientProvider client={client}><AIOptionsPage /></QueryClientProvider>);
}

test("FX3b 5.4: a voice list that failed (a 500) says the voices could not be loaded, not that they took too long", async () => {
    mount(async () => { throw new ApiClientError({ code: "server_error", message: "Internal Server Error", status: 500, url: "/ai-options/voices", method: "GET" }); });
    const sentence = await screen.findByText(FAILED_SENTENCE, undefined, { timeout: SLOW });
    assert.equal(sentence.getAttribute("role"), "alert");
    assert.equal(shown(TIMEOUT_SENTENCE), false);
    assert.equal(screen.queryByRole("button", { name: "Retry" }) !== null, true);
});

test("FX3b 5.4: a voice list that timed out keeps its own sentence", async () => {
    mount(async () => { throw new ApiClientError({ code: "timeout", message: "Request timed out", url: "/ai-options/voices", method: "GET" }); });
    await waitFor(() => assert.equal(shown(TIMEOUT_SENTENCE), true), { timeout: SLOW });
    assert.equal(shown(FAILED_SENTENCE), false);
});

test("FX3b2 Task 3: the model names show the provider id capitalised, like the voice provider buttons", async () => {
    mock.method(sharedHttpClient(), "request", async (request: Request) => {
        if (request.path.endsWith("/providers")) return { ...providers, llm: { providers: ["cerebras", "groq"], models: [{ id: "llama", name: "Llama 3.3", description: "Fast", provider: "groq" }] } };
        if (request.path.endsWith("/voices")) return { voices: [{ id: "voice", name: "Listed Voice", description: "Test", provider: "elevenlabs" }] };
        return stored;
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { gcTime: Infinity } } });
    clients.push(client);
    render(<QueryClientProvider client={client}><AIOptionsPage /></QueryClientProvider>);
    const select = await screen.findByRole("combobox", { name: "Model" }, { timeout: SLOW });
    // The saved model is not in the list: its own provider id is capitalised too.
    assert.equal(select.textContent?.includes("[Cerebras] gpt-oss-120b (currently unavailable)"), true, select.textContent ?? "");
    fireEvent.click(select);
    const options = within(screen.getByRole("listbox")).getAllByRole("option").map((option) => option.textContent?.trim());
    assert.equal(options.includes("[Groq] Llama 3.3"), true, options.join(" | "));
    assert.equal(options.some((option) => /\[(groq|cerebras)\]/.test(option ?? "")), false, options.join(" | "));
});
