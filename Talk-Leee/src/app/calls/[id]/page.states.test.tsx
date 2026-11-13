/**
 * FX2b Task 3.3 — call details uses the shared states: page loading is a
 * status, the page error and the transcript error show their message with
 * "Retry", and a failed recording is announced (role="alert").
 */
import Module from "node:module";
import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { z } from "zod";

import { extendedApi } from "@/lib/extended-api";

type Q = Record<string, unknown>;
let callState: Q = {};
let transcriptState: Q = {};
const call = {
    id: "call-1", status: "completed", direction: "outbound", phone_number: "+447400123456",
    created_at: "2026-01-01T00:00:00Z", duration_seconds: 65, recording_id: "rec-1",
};
const idleTranscript = { data: { turns: [] }, isLoading: false, isError: false, error: null, refetch: () => {} };

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { useParams: () => ({ id: "call-1" }), useRouter: () => ({ push: () => {}, back: () => {} }) };
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/calls/voice-feedback-recorder")) return { VoiceFeedbackRecorder: nothing };
    if (id.includes("components/calls/conversation-review-panel")) return { ConversationReviewPanel: nothing };
    if (id.includes("components/calls/lead-details-panel")) return { LeadDetailsPanel: nothing };
    if (id.includes("lib/queries/inbound-queries")) return { useEffectivePermissions: () => ({ isSuccess: true, isError: false, data: { permissions: ["recordings:read"] } }) };
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

const SLOW = 15_000;
afterEach(() => { cleanup(); mock.restoreAll(); callState = {}; transcriptState = {}; });
const shown = (text: string | RegExp) => screen.queryByText(text) !== null;
const status = (title: string) => screen.queryAllByRole("status").some((node) => within(node).queryByText(title) !== null);

test("call details: loading is a status (the shared LoadingState)", () => {
    callState = { isLoading: true, isError: false };
    transcriptState = idleTranscript;
    render(<Page />);
    assert.equal(status("Loading"), true);
});

test("call details: the page error shows the server's message with Retry, which reads again", () => {
    let reads = 0;
    callState = { isLoading: false, isError: true, error: new Error("Call store is down."), refetch: () => { reads += 1; } };
    transcriptState = idleTranscript;
    render(<Page />);
    assert.equal(screen.getByText("Call store is down.").getAttribute("role"), "alert");
    assert.equal(screen.queryByRole("button", { name: "Try again" }) === null, true);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    assert.equal(reads, 1);
});

test("call details: an answer the app could not read shows 'Please retry.', never the validation dump", () => {
    const parsed = z.object({ id: z.string() }).safeParse({ id: 1 });
    assert.equal(parsed.success, false);
    callState = { isLoading: false, isError: true, error: parsed.error, refetch: () => {} };
    transcriptState = idleTranscript;
    render(<Page />);
    assert.equal(shown("Please retry."), true);
    assert.equal(shown(/invalid_type|expected string/i), false);
});

test("call details: the transcript loading is a status titled 'Loading transcript…'", () => {
    callState = { data: call, isLoading: false, isError: false };
    transcriptState = { data: undefined, isLoading: true, isError: false, error: null, refetch: () => {} };
    render(<Page />);
    assert.equal(status("Loading transcript…"), true);
});

test("call details: the transcript error shows its message with Retry, which reads again", () => {
    let reads = 0;
    callState = { data: call, isLoading: false, isError: false };
    transcriptState = { data: undefined, isLoading: false, isError: true, error: new Error("Transcript store is down."), refetch: () => { reads += 1; } };
    render(<Page />);
    assert.equal(screen.getByText("Transcript store is down.").getAttribute("role"), "alert");
    assert.equal(shown("No transcript available"), false);
    assert.equal(screen.queryByRole("button", { name: "Try again" }) === null, true);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    assert.equal(reads, 1);
});

test("call details: a transcript answer the app could not read shows 'Please retry.', never the validation dump", () => {
    const parsed = z.object({ turns: z.array(z.string()) }).safeParse({ turns: 1 });
    assert.equal(parsed.success, false);
    callState = { data: call, isLoading: false, isError: false };
    transcriptState = { data: undefined, isLoading: false, isError: true, error: parsed.error, refetch: () => {} };
    render(<Page />);
    assert.equal(shown("Please retry."), true);
    assert.equal(shown(/invalid_type|expected array/i), false);
});

test("call details: a failed recording is announced (role=alert)", async () => {
    callState = { data: call, isLoading: false, isError: false };
    transcriptState = idleTranscript;
    mock.method(extendedApi, "fetchRecordingPlaybackBlob", async () => { throw new Error("Recording is unavailable."); });
    render(<Page />);
    fireEvent.click(screen.getByRole("button", { name: "Play" }));
    const message = await screen.findByText("Recording is unavailable.", undefined, { timeout: SLOW });
    assert.equal(message.getAttribute("role"), "alert");
});
