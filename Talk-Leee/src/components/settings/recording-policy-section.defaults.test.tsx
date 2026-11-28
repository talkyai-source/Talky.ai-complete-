import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import {
    RecordingPolicyView,
    type RecordingConsentMode,
    type RecordingPolicy,
    type RecordingPolicyInput,
} from "@/components/settings/recording-policy-section";
import { ensureDom } from "@/test-utils/dom";
import { renderWithQueryClient } from "@/test-utils/render";

ensureDom();
afterEach(() => cleanup());

// Backend rule since 18 Nov 2026: no saved policy = recorded, with a spoken notice.
const ABSENT: RecordingPolicy = { configured: false, effect: "No policy saved: calls are recorded, and the agent speaks a recording notice first." };

function saved(mode: RecordingConsentMode, extra: Partial<RecordingPolicy> = {}): RecordingPolicy {
    return {
        configured: true,
        default_consent_mode: mode,
        announcement_text: null,
        opt_out_dtmf_digit: null,
        two_party_country_codes: [],
        retention_days: 90,
        updated_at: "2026-11-01T00:00:00Z",
        effect: "x",
        ...extra,
    };
}

function render(policy: RecordingPolicy, canEdit = true) {
    const sent: RecordingPolicyInput[] = [];
    renderWithQueryClient(
        createElement(RecordingPolicyView, {
            policy,
            canEdit,
            onSave: async (input) => {
                sent.push(input);
                return undefined;
            },
        })
    );
    return sent;
}

const modeBox = () => screen.getByRole("combobox", { name: "Recording consent mode" });

test("T1: with no saved policy the form starts on the mode in force (two-party), not 'Do not record'", () => {
    render(ABSENT);
    assert.equal(modeBox().textContent, "Record with a spoken notice (two-party consent)");
});

test("T2: with no saved policy, changing only the retention and saving sends two_party, never disabled", async () => {
    const sent = render(ABSENT);
    fireEvent.change(screen.getByLabelText("Keep for (days)"), { target: { value: "30" } });
    fireEvent.click(screen.getByTestId("recording-policy-save"));
    await waitFor(() => assert.equal(sent.length, 1));
    assert.deepEqual(sent[0], {
        default_consent_mode: "two_party",
        announcement_text: null,
        opt_out_dtmf_digit: null,
        two_party_country_codes: [],
        retention_days: 30,
    });
});

for (const mode of ["disabled", "one_party", "two_party"] as const) {
    test(`T3: a saved ${mode} policy starts on ${mode} and an unrelated edit saves ${mode}`, async () => {
        const sent = render(saved(mode));
        const label = {
            disabled: "Do not record calls",
            one_party: "Record without a spoken notice (one-party consent)",
            two_party: "Record with a spoken notice (two-party consent)",
        }[mode];
        assert.equal(modeBox().textContent, label);
        fireEvent.change(screen.getByLabelText("Keep for (days)"), { target: { value: "45" } });
        fireEvent.click(screen.getByTestId("recording-policy-save"));
        await waitFor(() => assert.equal(sent.length, 1));
        assert.equal(sent[0].default_consent_mode, mode);
        assert.equal(sent[0].retention_days, 45);
    });
}

test("T4: the description says calls are recorded with a notice when no policy is saved, not that recording is off", () => {
    render(ABSENT);
    const text = screen.getByTestId("recording-policy").textContent ?? "";
    assert.doesNotMatch(text, /Recording is off until a policy is saved/);
    assert.match(text, /With no saved policy, calls are recorded and the agent speaks a recording notice first; an incoming line is recorded only while its own "Record inbound calls" switch is on\./);
    assert.match(text, /only tenant admins can change it/);
});

test("T5: the opt-out key is disabled for an administrator, explained, still shown and sent back unchanged", async () => {
    const sent = render(saved("two_party", { opt_out_dtmf_digit: "7" }));
    const box = screen.getByLabelText("Opt-out key") as HTMLInputElement;
    assert.equal(box.disabled, true);
    assert.equal(box.value, "7");
    assert.equal(box.getAttribute("placeholder"), null);
    assert.match(screen.getByTestId("recording-policy").textContent ?? "", /No effect in this release: callers cannot opt out by pressing a key\./);
    const notice = screen.getByLabelText("Spoken notice (two-party)") as HTMLInputElement;
    assert.doesNotMatch(notice.placeholder, /Press 9/);
    fireEvent.change(screen.getByLabelText("Keep for (days)"), { target: { value: "20" } });
    fireEvent.click(screen.getByTestId("recording-policy-save"));
    await waitFor(() => assert.equal(sent.length, 1));
    assert.equal(sent[0].opt_out_dtmf_digit, "7");
});

test("T6: Save stays disabled while the policy has not loaded; a loaded 'no policy' answer can still save", async () => {
    const sent: RecordingPolicyInput[] = [];
    renderWithQueryClient(
        createElement(RecordingPolicyView, {
            policy: undefined,
            canEdit: true,
            onSave: async (input) => {
                sent.push(input);
                return undefined;
            },
        })
    );
    fireEvent.change(screen.getByLabelText("Keep for (days)"), { target: { value: "30" } });
    const btn = screen.getByTestId("recording-policy-save") as HTMLButtonElement;
    assert.equal(btn.disabled, true);
    fireEvent.click(btn);
    assert.equal(sent.length, 0);
    cleanup();

    render(ABSENT);
    fireEvent.change(screen.getByLabelText("Keep for (days)"), { target: { value: "30" } });
    assert.equal((screen.getByTestId("recording-policy-save") as HTMLButtonElement).disabled, false);
});

test("T7: the mode help names the unknown-country rule for both modes", () => {
    render(ABSENT);
    const text = screen.getByTestId("recording-policy").textContent ?? "";
    assert.match(text, /One-party:[^.]*;[^.]*two-party country[^.]*not known\./);
    assert.match(text, /Two-party: the notice is spoken first when the list below is empty, when the callee's country is on it, or when the callee's country is not known\./);
});

test("T8: a non-administrator sees an account with no saved policy as two-party, with nothing editable", () => {
    render(ABSENT, false);
    assert.equal(modeBox().textContent, "Record with a spoken notice (two-party consent)");
    assert.equal(screen.queryByTestId("recording-policy-save"), null);
    assert.match(screen.getByTestId("recording-policy").textContent ?? "", /Only tenant admins can change this\./);
    for (const label of ["Spoken notice (two-party)", "Countries needing the notice", "Opt-out key", "Keep for (days)"]) {
        assert.equal((screen.getByLabelText(label) as HTMLInputElement).disabled, true, label);
    }
    assert.equal((modeBox() as HTMLButtonElement).disabled, true);
});
