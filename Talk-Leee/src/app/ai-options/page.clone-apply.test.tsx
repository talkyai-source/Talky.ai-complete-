/**
 * A new voice clone is offered to campaigns straight away (the existing "Apply
 * this voice to campaigns?" pop-up opens for it), and the voice section says
 * that a campaign's own voice wins. Same boundary stubs as page.fx3b-text.test.tsx.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { sharedHttpClient } from "@/lib/api";
import type { AIProviderConfig } from "@/lib/ai-options-api";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id.includes("hooks/useAuth")) return { useAuth: () => ({ user: { id: "user-a", tenant_id: "tenant-a", role: "tenant_admin" }, status: "authenticated", loading: false }) };
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/campaigns/apply-to-campaigns-modal")) {
        return {
            ApplyToCampaignsModal: ({ open, provider, voiceId, voiceLabel }: { open: boolean; provider: string; voiceId: string; voiceLabel?: string }) =>
                open ? <div data-testid="apply-popup">{`${provider}|${voiceId}|${voiceLabel}`}</div> : null,
        };
    }
    if (id.includes("components/ai-options/voice-clone-modal")) {
        return {
            VoiceCloneModal: ({ onCloned }: { onCloned?: (v: { voiceId: string; name: string }) => void }) => (
                <button type="button" onClick={() => onCloned?.({ voiceId: "new-clone-id", name: "My new clone" })}>fake clone done</button>
            ),
        };
    }
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
type Request = { path: string; method?: string };

function mount() {
    mock.method(sharedHttpClient(), "request", async (request: Request) => {
        if (request.path.endsWith("/providers")) return providers;
        if (request.path.endsWith("/voices")) return { voices: [] };
        return stored;
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { gcTime: Infinity } } });
    clients.push(client);
    render(<QueryClientProvider client={client}><AIOptionsPage /></QueryClientProvider>);
}

test("after a clone is created the Apply pop-up opens for the new voice, named", async () => {
    mount();
    const done = await screen.findByRole("button", { name: "fake clone done" }, { timeout: SLOW });
    assert.equal(screen.queryByTestId("apply-popup") === null, true);
    fireEvent.click(done);
    const popup = await screen.findByTestId("apply-popup");
    assert.equal(popup.textContent, "elevenlabs|new-clone-id|My new clone");
});

test("the voice section says a campaign's own voice is kept", async () => {
    mount();
    const sentence = await screen.findByText(
        "This is the default for new campaigns. A campaign that already has its own voice keeps it; change it on the campaign, or use Apply to campaigns after saving.",
        undefined, { timeout: SLOW },
    );
    assert.ok(sentence);
});
