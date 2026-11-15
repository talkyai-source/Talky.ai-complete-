/**
 * FX5 Task B6 — when the server answers realtime.available=false the Realtime
 * option is disabled with the server's own reason, the way an unavailable
 * transcription engine is shown. A click does nothing.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
const base = {
    llm: { providers: ["cerebras"], models: [] }, tts: { providers: ["elevenlabs"], models: [] },
    stt: { providers: ["deepgram"], models: [], engines: [{ id: "deepgram_nova", name: "Deepgram Nova", description: "Nova" }] },
};
const voiceList = { voices: [{ id: "voice", name: "Listed Voice", description: "Test", provider: "elevenlabs" }] };
type Request = { path: string; method?: string };

function mount(realtime: Record<string, unknown>) {
    mock.method(sharedHttpClient(), "request", async (request: Request) => {
        if (request.path.endsWith("/providers")) return { ...base, realtime };
        if (request.path.endsWith("/voices")) return voiceList;
        return stored;
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { gcTime: Infinity } } });
    clients.push(client);
    render(<QueryClientProvider client={client}><AIOptionsPage /></QueryClientProvider>);
}
const pageShown = () => waitFor(() => assert.equal(screen.queryByRole("combobox", { name: "Engine" }) !== null, true), { timeout: SLOW });
const realtimeButton = () => screen.getByRole("button", { name: /^Realtime \(speech-to-speech\)/ }) as HTMLButtonElement;
const standardButton = () => screen.getByRole("button", { name: "Standard Pipeline" }) as HTMLButtonElement;

test("realtime.available=false: the option is disabled, names the server's reason, and a click does nothing", async () => {
    mount({ available: false, unavailable_reason: "OpenAI is not configured", model: "gpt-realtime", voices: [] });
    await pageShown();
    const button = realtimeButton();
    assert.equal(button.disabled, true);
    assert.equal(button.textContent, "Realtime (speech-to-speech) (unavailable)", "marked the way an unavailable engine is");
    const reason = document.getElementById(button.getAttribute("aria-describedby") ?? "");
    assert.equal(reason !== null, true, "the button names its reason");
    assert.equal(reason!.textContent, "Realtime (speech-to-speech): OpenAI is not configured");

    fireEvent.click(button);
    assert.equal(button.getAttribute("aria-pressed"), "false", "the mode did not change");
    assert.equal(standardButton().getAttribute("aria-pressed"), "true");
    assert.equal(screen.queryByRole("alert") === null, true, "no error banner from a click that does nothing");
    assert.equal(standardButton().disabled, false, "the available option stays usable");
});

test("realtime.available=false with no reason from the server: the existing text is used", async () => {
    mount({ available: false, unavailable_reason: null, model: "gpt-realtime", voices: [] });
    await pageShown();
    const reason = document.getElementById(realtimeButton().getAttribute("aria-describedby") ?? "");
    assert.equal(reason?.textContent, "Realtime (speech-to-speech): OpenAI is not configured");
});

test("realtime.available=true: the option is offered as before and switches the mode", async () => {
    mount({ available: true, model: "gpt-realtime", voices: [{ id: "marin", name: "Marin" }] });
    await pageShown();
    const button = realtimeButton();
    assert.equal(button.disabled, false);
    assert.equal(button.textContent, "Realtime (speech-to-speech)");
    assert.equal(button.hasAttribute("aria-describedby"), false);
    assert.equal(screen.queryByText(/OpenAI is not configured/) === null, true);
    fireEvent.click(button);
    await waitFor(() => assert.equal(realtimeButton().getAttribute("aria-pressed"), "true"), { timeout: SLOW });
});
