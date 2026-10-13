import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import React, { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AssemblyAIControls } from "./assemblyai-controls";
import { assemblyAISettingsError, type AssemblyAISettings } from "@/lib/assemblyai-settings";

// JSDOM does not draw canvas; the actual Select retains its fallback positioning.
beforeEach(() => mock.method(window.HTMLCanvasElement.prototype, "getContext", () => null));
afterEach(() => { cleanup(); mock.restoreAll(); });

test("every AssemblyAI control edits its saved value and dependent values survive disabling and reopening", async () => {
    let draft: AssemblyAISettings = {};
    function Editor() {
        const [value, setValue] = useState(draft);
        return <AssemblyAIControls value={value} onChange={(next) => { draft = next; setValue(next); }} />;
    }
    let view = render(<Editor />);
    const select = async (label: string, option: string) => {
        fireEvent.click(screen.getByLabelText(label));
        fireEvent.click(await screen.findByRole("option", { name: option }));
    };
    const number = (label: string, value: number) => fireEvent.change(screen.getByLabelText(label), { target: { value: String(value) } });
    const toggle = (label: string) => fireEvent.click(screen.getByLabelText(label));

    assert.equal((screen.getByLabelText("Transcription language") as HTMLInputElement).disabled, true);
    await select("AssemblyAI mode", "Accuracy");
    await select("AssemblyAI region", "European Union");
    number("Minimum turn silence (ms)", 700);
    number("Maximum turn silence (ms)", 2400);
    number("Interruption delay (ms)", 0);
    number("Speech detection threshold", 0.25);
    await select("Partial transcripts", "Off");
    fireEvent.change(screen.getByLabelText("Audio context prompt"), { target: { value: "English dental appointment calls." } });
    fireEvent.change(screen.getByLabelText("Keyterms"), { target: { value: "  Acme  \n\n Dr Patel " } });
    fireEvent.blur(screen.getByLabelText("Keyterms"));
    toggle("Use agent replies as recognition context");
    number("Previous conversation turns", 0);
    toggle("Report detected language");
    await select("Voice Focus", "Near field");
    assert.equal((screen.getByLabelText("Voice Focus threshold") as HTMLInputElement).disabled, false);
    number("Voice Focus threshold", 0.8);
    await select("Vocabulary domain", "Medical");
    toggle("Speaker labels");
    assert.equal((screen.getByRole("group", { name: "Speaker label settings", hidden: true }) as HTMLFieldSetElement).disabled, false);
    number("Maximum speakers", 3);
    number("Speaker label revision interval (ms)", 120000);
    toggle("Redact personal information (PII)");
    toggle("credit card number");
    toggle("password");
    await select("PII replacement", "Entity labels ([EMAIL_ADDRESS])");
    toggle("Filter profanity");
    toggle("Session heartbeat");
    number("Audio inactivity timeout (seconds)", 120);
    const expected: AssemblyAISettings = {
        region: "eu", mode: "max_accuracy", min_turn_silence: 700, max_turn_silence: 2400,
        interruption_delay: 0, vad_threshold: 0.25, include_partial_turns: false,
        prompt: "English dental appointment calls.", keyterms_prompt: ["Acme", "Dr Patel"],
        auto_agent_context: false, previous_context_n_turns: 0, language_detection: true,
        voice_focus: "near-field", voice_focus_threshold: 0.8, domain: "medical-v1",
        speaker_labels: true, max_speakers: 3, speaker_labels_revision_interval_ms: 120000,
        redact_pii: true, redact_pii_policies: ["credit_card_number", "password"], redact_pii_sub: "entity_name",
        filter_profanity: true, session_heartbeat: false, inactivity_timeout: 120,
    };
    assert.deepEqual(draft, expected);
    assert.equal(assemblyAISettingsError(draft), null);

    await select("Voice Focus", "Off");
    toggle("Speaker labels");
    toggle("Redact personal information (PII)");
    view.unmount();
    view = render(<Editor />);
    assert.equal((screen.getByLabelText("Voice Focus threshold") as HTMLInputElement).value, "0.8");
    assert.equal((screen.getByLabelText("Maximum speakers") as HTMLInputElement).value, "3");
    assert.equal((screen.getByLabelText("credit card number") as HTMLInputElement).checked, true);
    await select("Voice Focus", "Near field");
    toggle("Speaker labels");
    toggle("Redact personal information (PII)");
    assert.deepEqual(draft, expected);

    await select("Partial transcripts", "Auto (provider default)");
    assert.equal(draft.include_partial_turns, null);
    fireEvent.click(screen.getByRole("button", { name: "Clear selection and use all types", hidden: true }));
    assert.deepEqual(draft.redact_pii_policies, []);
    view.unmount();
});

test("PII redaction explains lead capture loss and unredacted partials; dependent choices survive toggles", () => {
    let draft: AssemblyAISettings = { include_partial_turns: true, voice_focus_threshold: 0.8, max_speakers: 2 };
    function Editor() {
        const [value, setValue] = useState(draft);
        return <AssemblyAIControls value={value} onChange={(next) => { draft = next; setValue(next); }} />;
    }
    render(<Editor />);
    assert.equal((screen.getByLabelText("Voice Focus threshold") as HTMLInputElement).disabled, true);
    assert.equal((screen.getByRole("group", { name: "PII redaction settings", hidden: true }) as HTMLFieldSetElement).disabled, true);
    fireEvent.click(screen.getByLabelText("Redact personal information (PII)"));
    assert.equal(draft.redact_pii, true);
    assert.match(screen.getByRole("alert", { hidden: true }).textContent || "", /unredacted personal details/);
    assert.match(screen.getByRole("status", { hidden: true }).textContent || "", /cannot be recovered for leads/);
    fireEvent.click(screen.getByLabelText("credit card number"));
    assert.deepEqual(draft.redact_pii_policies, ["credit_card_number"]);
    fireEvent.click(screen.getByLabelText("Redact personal information (PII)"));
    assert.deepEqual(draft.redact_pii_policies, ["credit_card_number"]);
    assert.equal(draft.voice_focus_threshold, 0.8);
    assert.equal(draft.max_speakers, 2);
});

test("blank numeric overrides preserve provider defaults and invalid configurations have actionable errors", () => {
    let draft: AssemblyAISettings = { mode: "max_accuracy", min_turn_silence: 800, max_turn_silence: 900 };
    function Editor() {
        const [value, setValue] = useState(draft);
        return <AssemblyAIControls value={value} onChange={(next) => { draft = next; setValue(next); }} />;
    }
    render(<Editor />);
    fireEvent.change(screen.getByLabelText("Minimum turn silence (ms)"), { target: { value: "" } });
    assert.equal(draft.min_turn_silence, null);
    assert.equal(draft.mode, "max_accuracy");
    assert.equal(assemblyAISettingsError(draft), null);
    assert.match(assemblyAISettingsError({ min_turn_silence: 1000, max_turn_silence: 500 }) || "", /at least the minimum/);
    assert.match(assemblyAISettingsError({ interruption_delay: 1001 }) || "", /interruption delay/);
    assert.match(assemblyAISettingsError({ speaker_labels_revision_interval_ms: 1000 }) || "", /at least 120000/);
    assert.match(assemblyAISettingsError({ keyterms_prompt: ["x".repeat(51)] }) || "", /keyterms prompt/);
});
