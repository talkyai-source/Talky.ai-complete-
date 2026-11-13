/**
 * FX3b Task 2 — call details texts: the outcome uses the Call History label
 * (humanizeOutcome), the speakers are "Agent" and "Caller" as in the list,
 * the back button names the page "Call History", and an inbound call's two
 * numbers are both called "Phone Number".
 * Same boundary stubs as page.states.test.tsx.
 */
import Module from "node:module";
import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";

import { humanizeOutcome } from "@/lib/call-outcome-label";

type Q = Record<string, unknown>;
let callState: Q = {};
let transcriptState: Q = {};
const call = {
    id: "call-1", status: "completed", direction: "inbound", phone_number: "+447400123456", to_number: "+442079460999",
    created_at: "2026-01-01T00:00:00Z", duration_seconds: 65, outcome: "no_answer",
};

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { useParams: () => ({ id: "call-1" }), useRouter: () => ({ push: () => {}, back: () => {} }) };
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/calls/voice-feedback-recorder")) return { VoiceFeedbackRecorder: nothing };
    if (id.includes("components/calls/conversation-review-panel")) return { ConversationReviewPanel: nothing };
    if (id.includes("components/calls/lead-details-panel")) return { LeadDetailsPanel: nothing };
    if (id.includes("lib/queries/inbound-queries")) return { useEffectivePermissions: () => ({ isSuccess: true, isError: false, data: { permissions: [] } }) };
    if (id.endsWith("lib/api-hooks")) return { useCall: () => callState, useCallTranscript: () => transcriptState };
    return originalLoad.call(this, id, ...args);
};
let Page: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Page = require("./page").default;
} finally {
    loader._load = originalLoad;
}

const originalRequestAnimationFrame = window.requestAnimationFrame;
const originalCancelAnimationFrame = window.cancelAnimationFrame;
const originalScrollTo = window.Element.prototype.scrollTo;
beforeEach(() => {
    // The transcript list measures itself on the next frame; jsdom here has no frames.
    window.requestAnimationFrame = (callback) => window.setTimeout(() => callback(performance.now()), 0);
    window.cancelAnimationFrame = (handle) => window.clearTimeout(handle);
    window.Element.prototype.scrollTo = () => {};
});
afterEach(() => {
    cleanup(); mock.restoreAll(); callState = {}; transcriptState = {};
    window.requestAnimationFrame = originalRequestAnimationFrame;
    window.cancelAnimationFrame = originalCancelAnimationFrame;
    window.Element.prototype.scrollTo = originalScrollTo;
});
const count = (text: string) => screen.queryAllByText(text).length;

function mount(outcome = "no_answer") {
    callState = { data: { ...call, outcome }, isLoading: false, isError: false };
    transcriptState = {
        data: { turns: [{ role: "assistant", content: "Hello there" }, { role: "user", content: "Hi" }] },
        isLoading: false, isError: false, error: null, refetch: () => {},
    };
    render(<Page />);
}

test("FX3b 2.1: the outcome is printed with the Call History label", () => {
    mount("no_answer");
    assert.equal(humanizeOutcome("no_answer"), "No Answer");
    assert.equal(count("No Answer"), 1);
    assert.equal(count("no answer"), 0);
    cleanup();
    mount("customer_hung_up");
    assert.equal(count("Customer Hung Up"), 1);
    assert.equal(count("customer hung up"), 0);
});

test("FX3b 2.6: the transcript speakers are 'Agent' and 'Caller'", () => {
    mount();
    assert.equal(count("Hello there"), 1);
    assert.equal(count("Agent"), 1);
    assert.equal(count("Caller"), 1);
    assert.equal(count("AI"), 0);
    assert.equal(count("U"), 0);
});

test("FX3b 2.7: the back button names the page 'Call History'", () => {
    mount();
    assert.equal(screen.queryByRole("button", { name: "Back to Call History" }) !== null, true);
    assert.equal(screen.queryByRole("button", { name: "Back to calls" }) !== null, false);
});

test("FX3b 2.4: an inbound call's numbers are 'Caller Phone Number' and 'Called Phone Number'", () => {
    mount();
    assert.equal(count("Caller Phone Number"), 1);
    assert.equal(count("Called Phone Number"), 1);
    assert.equal(count("Caller ANI"), 0);
    assert.equal(count("Called DID"), 0);
});
