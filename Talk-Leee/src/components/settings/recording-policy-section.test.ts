import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { RecordingPolicyView, type RecordingPolicy, type RecordingPolicyInput } from "@/components/settings/recording-policy-section";
import { ensureDom } from "@/test-utils/dom";
import { renderWithQueryClient } from "@/test-utils/render";

ensureDom();
afterEach(() => cleanup());

const ABSENT: RecordingPolicy = { configured: false, effect: "No policy configured: calls are NOT recorded until one is saved." };
const ONE_PARTY: RecordingPolicy = {
    configured: true,
    default_consent_mode: "one_party",
    retention_days: 90,
    two_party_country_codes: [],
    updated_at: "2026-09-07T00:00:00Z",
    effect: "Calls are recorded without a spoken notice.",
};

function render(policy: RecordingPolicy, canEdit = true) {
    const saved: RecordingPolicyInput[] = [];
    renderWithQueryClient(
        createElement(RecordingPolicyView, {
            policy,
            canEdit,
            onSave: async (input) => {
                saved.push(input);
                return undefined;
            },
        })
    );
    return saved;
}

test("an absent policy is shown as recording OFF", () => {
    render(ABSENT);
    assert.match(screen.getByTestId("recording-policy-effect").textContent ?? "", /NOT recorded/);
    assert.equal((screen.getByTestId("recording-policy-save") as HTMLButtonElement).disabled, true);
});

test("non-admins see the policy but cannot save", () => {
    render(ONE_PARTY, false);
    assert.equal(screen.queryByTestId("recording-policy-save"), null);
    assert.match(screen.getByTestId("recording-policy").textContent ?? "", /Only tenant admins/);
});

test("changing retention enables Save and sends the full policy", async () => {
    const saved = render(ONE_PARTY);
    fireEvent.change(screen.getByLabelText("Keep for (days)"), { target: { value: "30" } });
    const btn = screen.getByTestId("recording-policy-save") as HTMLButtonElement;
    assert.equal(btn.disabled, false);
    fireEvent.click(btn);
    await waitFor(() => assert.equal(saved.length, 1));
    assert.deepEqual(saved[0], {
        default_consent_mode: "one_party",
        announcement_text: null,
        opt_out_dtmf_digit: null,
        two_party_country_codes: [],
        retention_days: 30,
    });
});

test("two-party fields are disabled unless the mode is two-party", () => {
    render(ONE_PARTY);
    assert.equal((screen.getByLabelText("Spoken notice (two-party)") as HTMLInputElement).disabled, true);
    assert.equal((screen.getByLabelText("Countries needing the notice") as HTMLInputElement).disabled, true);
});
