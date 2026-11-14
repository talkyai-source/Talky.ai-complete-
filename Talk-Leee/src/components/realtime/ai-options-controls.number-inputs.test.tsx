import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React, { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { AIProviderConfig } from "@/lib/ai-options-api";
import { RealtimeControls } from "./ai-options-controls";

// Forms audit B-F24: an emptied Speech speed / Maximum response tokens box used
// to send 0, which the server refuses (speed >= 0.25, tokens >= 64).
afterEach(cleanup);
const initial = {
    pipeline_mode: "realtime", realtime_model: "gpt-realtime-2", realtime_voice: "ash",
    realtime_settings: { speed: 1, max_output_tokens: 1024 },
} as unknown as AIProviderConfig;
const catalog = { available: true, model: "gpt-realtime-2", voices: [{ id: "ash", name: "Ash", description: "Clear" }], turn_detection: ["medium"], noise_reduction: ["near_field"] };

function mount() {
    const saves: AIProviderConfig[] = [];
    function Harness() {
        const [config, setConfig] = useState(initial);
        return <RealtimeControls config={config} catalog={catalog} onChange={(next) => { saves.push(next); setConfig(next); }} onPreview={() => {}} previewing={false} />;
    }
    render(<Harness />);
    return saves;
}

test("clearing the box sends nothing, and blur restores the saved value", () => {
    const saves = mount();
    const speed = screen.getByLabelText("Speech speed") as HTMLInputElement;
    fireEvent.change(speed, { target: { value: "" } });
    assert.equal(saves.length, 0);
    assert.equal(speed.value, "");
    fireEvent.blur(speed);
    assert.equal(speed.value, "1");
    assert.equal(saves.length, 0);
});

test("an out-of-range value is not sent while typing and is clamped on blur", () => {
    const saves = mount();
    const tokens = screen.getByLabelText("Maximum response tokens") as HTMLInputElement;
    fireEvent.change(tokens, { target: { value: "5" } });
    assert.equal(saves.length, 0);
    fireEvent.blur(tokens);
    assert.equal(tokens.value, "64");
    assert.equal(saves.at(-1)?.realtime_settings?.max_output_tokens, 64);
});

test("a valid value is sent as typed", () => {
    const saves = mount();
    fireEvent.change(screen.getByLabelText("Speech speed"), { target: { value: "1.2" } });
    assert.equal(saves.at(-1)?.realtime_settings?.speed, 1.2);
});
