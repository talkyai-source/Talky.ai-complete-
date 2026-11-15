/**
 * FX4a Task 2 and Task 8 — on AI Options the "Turn-0 minimum confidence"
 * slider and the "Turn-0 minimum alpha chars" number box are named by the text
 * beside them, the LLM test box is named by its section's own sentence, and
 * the pipeline buttons report which one is selected.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { sharedHttpClient } from "@/lib/api";
import type { AIProviderConfig } from "@/lib/ai-options-api";
import { Segmented } from "@/components/ai-options/controls";

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


test("the Turn-0 slider and number box are named by the text beside them", async () => {
    await renderPage("tenant_admin");
    const slider = screen.getByRole("slider", { name: /^Turn-0 minimum confidence/ }) as HTMLInputElement;
    assert.equal(slider.type, "range");
    assert.equal(document.getElementById(slider.getAttribute("aria-labelledby")!)!.textContent, "Turn-0 minimum confidence default 0.4");
    const box = screen.getByRole("spinbutton", { name: /^Turn-0 minimum alpha chars/ }) as HTMLInputElement;
    assert.equal(box.type, "number");
    assert.equal(document.getElementById(box.getAttribute("aria-labelledby")!)!.textContent, "Turn-0 minimum alpha chars default 2");
});

test("the LLM test box is named by its section's sentence, which is on screen", async () => {
    await renderPage("tenant_admin");
    const box = screen.getByRole("textbox", { name: "Send a message to the selected model" }) as HTMLInputElement;
    assert.equal(box.getAttribute("placeholder"), "Type a message to test the LLM…");
    assert.equal(screen.queryAllByText("Send a message to the selected model").length, 1, "the name is the section's visible sentence");
});

test("the pipeline buttons report the selected one", async () => {
    await renderPage("tenant_admin");
    const standard = screen.getByRole("button", { name: "Standard Pipeline" });
    const realtime = screen.getByRole("button", { name: "Realtime (speech-to-speech)" });
    assert.equal(standard.getAttribute("aria-pressed"), "true");
    assert.equal(realtime.getAttribute("aria-pressed"), "false");
});

test("the button group's report follows the selection", () => {
    function Group() {
        const [value, setValue] = React.useState("cascaded");
        return <Segmented value={value} onChange={setValue} options={[{ value: "cascaded", label: "Standard Pipeline" }, { value: "realtime", label: "Realtime (speech-to-speech)" }]} />;
    }
    render(<Group />);
    const pressed = () => screen.getAllByRole("button").map((button) => `${button.textContent}:${button.getAttribute("aria-pressed")}`);
    assert.deepEqual(pressed(), ["Standard Pipeline:true", "Realtime (speech-to-speech):false"]);
    fireEvent.click(screen.getByRole("button", { name: "Realtime (speech-to-speech)" }));
    assert.deepEqual(pressed(), ["Standard Pipeline:false", "Realtime (speech-to-speech):true"]);
});

// FX4b Task 4 and FX4b2 Task 2 — the dials sit beside the model picker from
// 1024px up, as before; the picker fills its own place, and the full selected
// name is in its title.
test("FX4b: the model picker fills its place beside the dials and carries the full selected name as its title", async () => {
    await renderPage("tenant_admin");
    const picker = screen.getByRole("combobox", { name: "Model" });
    const shown = picker.textContent ?? "";
    assert.equal(shown.includes("gpt-oss-120b"), true);
    assert.equal(picker.getAttribute("title"), shown, "the title is the selected name itself");
    assert.equal(picker.classList.contains("w-full"), true);
    const row = picker.closest(".grid");
    assert.equal(row !== null, true);
    assert.equal(row!.className, "grid gap-5 lg:grid-cols-[1fr_auto]", "the picker-and-dials arrangement is the original one");
    const place = row!.firstElementChild as HTMLElement;
    assert.equal(place.contains(picker), true, "the picker is in the first place of that row");
    assert.equal(place.className, "space-y-3");
    const dials = Array.from(row!.children).filter((child) => (child.getAttribute("aria-label") ?? "").includes("creativity and max length dials"));
    assert.equal(dials.length, 1, "the dials are a place of the same row, after the picker's");
    assert.equal(dials[0] !== place && Array.from(row!.children).indexOf(dials[0]) > 0, true);
    // Nothing between the place and the picker narrows it: its wrapper is a plain block.
    assert.equal(picker.parentElement!.className, "relative");
    assert.equal(picker.parentElement!.parentElement === place, true);
    assert.equal(screen.getByRole("combobox", { name: "Engine" }).hasAttribute("title"), false, "other pickers are unchanged");
});
