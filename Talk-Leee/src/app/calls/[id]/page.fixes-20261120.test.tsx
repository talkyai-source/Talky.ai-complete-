import Module from "node:module";
import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { extendedApi } from "@/lib/extended-api";

const user = { id: "user-1", tenant_id: "tenant-1", account_timezone: "Europe/London" };
const call = {
    id: "call-1", status: "completed", direction: "outbound", phone_number: "+447400123456",
    created_at: "2026-07-01T12:00:00Z", duration_seconds: 65, recording_id: "rec-1",
};
const turns = [{ role: "assistant", content: "Hello there", timestamp: "2026-07-01T12:00:00Z" }];
const permissions = { isSuccess: true, isError: false, data: { permissions: ["recordings:read"] } };
const callResult = { data: call, isLoading: false, isError: false, error: null };
const transcriptResult = { data: { turns }, isLoading: false, isError: false, error: null, refetch: () => {} };

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    if (id.includes("lib/auth-context")) return { useAuth: () => ({ user }) };
    if (id === "next/navigation") return { useParams: () => ({ id: "call-1" }), useRouter: () => ({ push: () => {}, back: () => {} }) };
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/calls/voice-feedback-recorder")) return { VoiceFeedbackRecorder: nothing };
    if (id.includes("components/calls/conversation-review-panel")) return { ConversationReviewPanel: nothing };
    if (id.includes("components/calls/lead-details-panel")) return { LeadDetailsPanel: nothing };
    if (id.includes("lib/queries/inbound-queries")) return { useEffectivePermissions: () => permissions };
    if (id.endsWith("lib/api-hooks")) return { useCall: () => callResult, useCallTranscript: () => transcriptResult };
    return originalLoad.call(this, id, ...args);
};
let Page: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Page = require("./page").default;
} finally {
    loader._load = originalLoad;
}

const originalAudio = globalThis.Audio;
const originalCreate = URL.createObjectURL;
const originalRevoke = URL.revokeObjectURL;
if (!Element.prototype.scrollTo) Element.prototype.scrollTo = () => {};
const originalRaf = window.requestAnimationFrame;
const originalCaf = window.cancelAnimationFrame;
beforeEach(() => {
    window.requestAnimationFrame = (callback) => window.setTimeout(() => callback(performance.now()), 0);
    window.cancelAnimationFrame = (handle) => window.clearTimeout(handle);
});
afterEach(() => {
    cleanup();
    window.requestAnimationFrame = originalRaf;
    window.cancelAnimationFrame = originalCaf;
    mock.restoreAll();
    globalThis.Audio = originalAudio;
    URL.createObjectURL = originalCreate;
    URL.revokeObjectURL = originalRevoke;
});

test("Play, Pause, Play sets the audio source once, so a resume does not restart the recording", async () => {
    let srcSets = 0;
    class FakeAudio {
        private _src = "";
        onended: (() => void) | null = null;
        onpause: (() => void) | null = null;
        onplay: (() => void) | null = null;
        constructor(url: string) { this.src = url; }
        get src() { return this._src; }
        set src(value: string) { srcSets++; this._src = value; }
        async play() { this.onplay?.(); }
        pause() { this.onpause?.(); }
    }
    globalThis.Audio = FakeAudio as unknown as typeof Audio;
    URL.createObjectURL = () => "blob:rec-1";
    URL.revokeObjectURL = () => {};
    mock.method(extendedApi, "fetchRecordingPlaybackBlob", async () => new Blob(["x"]));
    render(<Page />);
    fireEvent.click(await screen.findByRole("button", { name: /^Play$/ }));
    fireEvent.click(await screen.findByRole("button", { name: /^Pause$/ }));
    fireEvent.click(await screen.findByRole("button", { name: /^Play$/ }));
    await waitFor(() => assert.equal(screen.queryByRole("button", { name: /^Pause$/ }) !== null, true));
    assert.equal(srcSets, 1);
});

test("transcript times use the account time zone", () => {
    render(<Page />);
    // 12:00 UTC on 1 July is 1:00 PM in London (BST), whatever zone the process runs in.
    assert.equal(screen.queryByText("1:00 PM") !== null, true);
});
