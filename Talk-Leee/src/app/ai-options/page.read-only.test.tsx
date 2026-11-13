/** FX1b Task 6 — a read-only role gets every control disabled; a failed voice list does not blame the saved voice. */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { sharedHttpClient } from "@/lib/api";
import type { AIProviderConfig } from "@/lib/ai-options-api";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
let identity = { user: { id: "user-a", tenant_id: "tenant-a", role: "tenant_admin" }, status: "authenticated", loading: false };
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

const originalScrollIntoView = window.Element.prototype.scrollIntoView;
beforeEach(() => {
    mock.method(window.HTMLCanvasElement.prototype, "getContext", () => null);
    window.Element.prototype.scrollIntoView = () => {};
});
afterEach(() => {
    cleanup();
    mock.restoreAll();
    window.Element.prototype.scrollIntoView = originalScrollIntoView;
    identity = { user: { id: "user-a", tenant_id: "tenant-a", role: "tenant_admin" }, status: "authenticated", loading: false };
});

const SAVED_VOICE_SENTENCE = /Saved voice .* is currently unavailable in the catalog/;

async function renderPage(role: string, voices: "ok" | "without-saved" | "fail" = "ok") {
    identity = { user: { id: "user-a", tenant_id: "tenant-a", role }, status: "authenticated", loading: false };
    const stored: AIProviderConfig = {
        llm_provider: "cerebras", llm_model: "gpt-oss-120b", llm_temperature: 0.6, llm_max_tokens: 100,
        stt_provider: "deepgram", stt_model: "nova-3", stt_engine: "deepgram_nova", stt_language: "en",
        tts_provider: "elevenlabs", tts_model: "m", tts_voice_id: "voice", tts_sample_rate: 16000,
    };
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    mock.method(sharedHttpClient(), "request", async (request: { path: string }) => {
        if (request.path.endsWith("/providers")) return {
            llm: { providers: ["cerebras"], models: [] }, tts: { providers: ["elevenlabs"], models: [] },
            stt: { providers: ["deepgram"], models: [], engines: [{ id: "deepgram_nova", name: "Deepgram Nova", description: "Nova" }, { id: "deepgram_flux", name: "Deepgram Flux", description: "Flux" }] },
        };
        if (request.path.endsWith("/voices")) {
            if (voices === "fail") throw new Error("voices unavailable");
            return { voices: [{ id: voices === "ok" ? "voice" : "other-voice", name: "Listed Voice", description: "Test", provider: "elevenlabs" }] };
        }
        return stored;
    });
    render(<QueryClientProvider client={qc}><AIOptionsPage /></QueryClientProvider>);
    await waitFor(() => assert.equal(screen.queryByRole("combobox", { name: "Engine" }) !== null, true));
}

test("for a role that cannot change AI settings every control on the page is disabled", async () => {
    const user = userEvent.setup({ document: globalThis.document });
    await renderPage("user");

    const formControls = Array.from(document.querySelectorAll("button, input, select, textarea"));
    assert.equal(formControls.length > 10, true);
    const stillEnabled = formControls.filter((control) => !control.matches(":disabled")).map((control) => control.getAttribute("aria-label") || control.textContent?.trim().slice(0, 40));
    assert.deepEqual(stillEnabled, []);

    // A select does not open.
    await user.click(screen.getByRole("combobox", { name: "Engine" }));
    assert.equal(screen.queryByRole("listbox") !== null, false);
    assert.equal(screen.getByRole("combobox", { name: "Engine" }).getAttribute("aria-expanded"), "false");

    // A dial does not move.
    const dial = screen.getByRole("slider", { name: "Temp" });
    assert.equal(dial.getAttribute("aria-disabled"), "true");
    assert.equal(dial.getAttribute("tabindex"), "-1");
    fireEvent.keyDown(dial, { key: "ArrowUp" });
    assert.equal(dial.getAttribute("aria-valuenow"), "0.6");
    const tokens = screen.getByRole("slider", { name: "Tokens" });
    fireEvent.keyDown(tokens, { key: "ArrowUp" });
    assert.equal(tokens.getAttribute("aria-valuenow"), "100");
});

test("a workspace admin keeps working controls", async () => {
    const user = userEvent.setup({ document: globalThis.document });
    await renderPage("tenant_admin");
    await user.click(screen.getByRole("combobox", { name: "Engine" }));
    assert.equal(screen.queryByRole("listbox") !== null, true);
    await user.keyboard("{Escape}");
    const dial = screen.getByRole("slider", { name: "Temp" });
    fireEvent.keyDown(dial, { key: "ArrowUp" });
    await waitFor(() => assert.equal(dial.getAttribute("aria-valuenow"), "0.7"));
});

test("when the voice list fails the saved-voice sentence is not shown", async () => {
    await renderPage("tenant_admin", "fail");
    await waitFor(() => assert.equal(screen.queryByText("Voices could not be loaded.") !== null, true));
    assert.equal(screen.queryByText(SAVED_VOICE_SENTENCE) !== null, false);
});

test("when the voice list loads without the saved voice the sentence is still shown", async () => {
    await renderPage("tenant_admin", "without-saved");
    await waitFor(() => assert.equal(screen.queryByText(SAVED_VOICE_SENTENCE) !== null, true));
});
