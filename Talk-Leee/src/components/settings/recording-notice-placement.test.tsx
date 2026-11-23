/**
 * Where the recording notice is said on outgoing calls: one control on the
 * recording policy section, shown only when the server sends the placement.
 */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { RecordingPolicySection } from "@/components/settings/recording-policy-section";
import { setRequestIdentity, setTokenProvider } from "@/lib/http-client";

const SLOW = 15_000;
const PLACEMENT_PATH = "/recordings/policy/outbound-notice-placement";
const OWN = "Acme records calls for training.";
const originalFetch = globalThis.fetch;
afterEach(() => {
    cleanup();
    globalThis.fetch = originalFetch;
    setTokenProvider(null);
});

const BASE = {
    configured: true, default_consent_mode: "two_party", announcement_text: OWN, opt_out_dtmf_digit: "9",
    two_party_country_codes: ["GB"], retention_days: 30, updated_at: "2026-01-01T00:00:00Z", effect: "Calls are recorded after a spoken notice.",
};
const WITH_PLACEMENT = { ...BASE, outbound_notice_placement: "own_sentence_first", outbound_notice_own_sentence: OWN };

type Call = { path: string; method: string; body: unknown };

function mockServer(getPolicy: Record<string, unknown>, placementAnswer?: () => Response) {
    const calls: Call[] = [];
    setTokenProvider(() => null);
    setRequestIdentity({ userId: "user-a", tenantId: "tenant-a" });
    globalThis.fetch = (async (url, init) => {
        const path = new URL(String(url)).pathname.replace(/^.*?(\/recordings)/, "$1");
        const method = String(init?.method ?? "GET").toUpperCase();
        let body: unknown;
        if (typeof init?.body === "string") body = JSON.parse(init.body);
        calls.push({ path, method, body });
        if (path === PLACEMENT_PATH) {
            assert.ok(placementAnswer, "no request to the new path was expected");
            return placementAnswer();
        }
        if (method === "PUT") return Response.json({ ...getPolicy, updated_at: "2026-01-02T00:00:00Z" });
        return Response.json(getPolicy);
    }) as typeof fetch;
    return calls;
}

const heading = () => screen.queryByText("Where the agent says the recording notice on outgoing calls");
const control = () => screen.queryByTestId("outbound-notice-placement");
const placementCalls = (calls: Call[]) => calls.filter((c) => c.path === PLACEMENT_PATH);

async function loaded(canEdit = true) {
    render(<RecordingPolicySection canEdit={canEdit} />);
    await screen.findByTestId("recording-policy-effect", undefined, { timeout: SLOW });
}

test("a server that does not send the placement shows no control and the main Save sends today's body only", async () => {
    const calls = mockServer(BASE);
    await loaded();
    assert.equal(control(), null);
    assert.equal(heading(), null);

    fireEvent.change(screen.getByLabelText("Keep for (days)"), { target: { value: "45" } });
    fireEvent.click(screen.getByTestId("recording-policy-save"));
    await waitFor(() => assert.equal(calls.filter((c) => c.method === "PUT").length, 1), { timeout: SLOW });
    const put = calls.find((c) => c.method === "PUT")!;
    assert.equal(put.path, "/recordings/policy");
    assert.deepEqual(put.body, {
        default_consent_mode: "two_party",
        announcement_text: OWN,
        opt_out_dtmf_digit: "9",
        two_party_country_codes: ["GB"],
        retention_days: 45,
    });
    assert.equal(placementCalls(calls).length, 0);
});

test("a null placement (server before its migration) hides the control", async () => {
    mockServer({ ...BASE, outbound_notice_placement: null, outbound_notice_own_sentence: null });
    await loaded();
    assert.equal(control(), null);
});

test("recording switched off hides the control", async () => {
    mockServer({ ...WITH_PLACEMENT, default_consent_mode: "disabled" });
    await loaded();
    assert.equal(control(), null);
});

test("both options show what the person will hear", async () => {
    mockServer(WITH_PLACEMENT);
    await loaded();
    assert.notEqual(heading(), null);
    const text = control()?.textContent ?? "";
    assert.ok(text.includes(`The person first hears: "${OWN}"`), text);
    assert.ok(text.includes("Your notice first, as its own sentence"));
    assert.ok(text.includes("Inside the opening sentence"));
    assert.ok(text.includes(", on a recorded line"));
    assert.ok(text.includes("not spoken on outgoing calls"));
    assert.ok(text.includes("Outgoing calls only"));
    assert.ok(text.includes("[agent name]") && text.includes("[company]") && text.includes("[first name]"));
    assert.equal((screen.getByLabelText("Your notice first, as its own sentence") as HTMLInputElement).checked, true);
});

test("choosing the opening sentence and saving sends one PUT and shows the answer", async () => {
    const answer = { ...WITH_PLACEMENT, outbound_notice_placement: "in_opening", updated_at: "2026-01-03T00:00:00Z" };
    const calls = mockServer(WITH_PLACEMENT, () => Response.json(answer));
    await loaded();
    const save = screen.getByTestId("outbound-notice-placement-save") as HTMLButtonElement;
    assert.equal(save.disabled, true, "unchanged choice");
    fireEvent.click(screen.getByLabelText("Inside the opening sentence"));
    assert.equal(save.disabled, false);
    fireEvent.click(save);
    await waitFor(() => assert.equal(placementCalls(calls).length, 1), { timeout: SLOW });
    assert.deepEqual(placementCalls(calls)[0], { path: PLACEMENT_PATH, method: "PUT", body: { outbound_notice_placement: "in_opening" } });
    await waitFor(() => assert.equal((screen.getByLabelText("Inside the opening sentence") as HTMLInputElement).checked, true), { timeout: SLOW });
    assert.equal((screen.getByTestId("outbound-notice-placement-save") as HTMLButtonElement).disabled, true, "now equals the saved choice");
    assert.equal(placementCalls(calls).length, 1);
    assert.equal(calls.filter((c) => c.method === "PUT" && c.path === "/recordings/policy").length, 0);
});

test("the button waits while the form above has unsaved changes", async () => {
    mockServer(WITH_PLACEMENT);
    await loaded();
    fireEvent.click(screen.getByLabelText("Inside the opening sentence"));
    assert.equal((screen.getByTestId("outbound-notice-placement-save") as HTMLButtonElement).disabled, false);
    fireEvent.change(screen.getByLabelText("Keep for (days)"), { target: { value: "45" } });
    assert.equal((screen.getByTestId("outbound-notice-placement-save") as HTMLButtonElement).disabled, true);
    assert.notEqual(screen.queryByText("Save or undo the changes above first."), null);
});

test("a person who is not an admin cannot change the choice", async () => {
    mockServer(WITH_PLACEMENT);
    await loaded(false);
    assert.notEqual(control(), null);
    assert.equal(screen.queryByTestId("outbound-notice-placement-save"), null);
    assert.equal((screen.getByLabelText("Inside the opening sentence") as HTMLInputElement).disabled, true);
});

test("the standard wording is already inside the opening: no radios, no button", async () => {
    mockServer({ ...WITH_PLACEMENT, outbound_notice_own_sentence: null });
    await loaded();
    assert.ok((control()?.textContent ?? "").includes("already said inside the opening sentence"));
    assert.equal(screen.queryByRole("radio"), null);
    assert.equal(screen.queryByTestId("outbound-notice-placement-save"), null);
});

test("a policy that is not saved yet says so", async () => {
    mockServer(WITH_PLACEMENT, () => Response.json({ detail: { code: "recording_policy_not_saved" } }, { status: 409 }));
    await loaded();
    fireEvent.click(screen.getByLabelText("Inside the opening sentence"));
    fireEvent.click(screen.getByTestId("outbound-notice-placement-save"));
    await screen.findByText("Save the recording policy first.", undefined, { timeout: SLOW });
});
