/** FX1b Task 2 — the "too short" sentence shows only after a too-short recording attempt. */
import Module from "node:module";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, screen, waitFor } from "@testing-library/react";

import { extendedApi } from "@/lib/extended-api";
import { renderWithQueryClient } from "@/test-utils/render";
import type { VoiceRecorderApi } from "@/components/calls/use-voice-recorder";

const TOO_SHORT = "That was too short to hear. Hold the mic a little longer.";

const idle: VoiceRecorderApi = {
    phase: "idle", clip: null, levels: [], seconds: 0, error: null,
    start: async () => {}, stop: () => {}, cancel: () => {}, discard: () => {}, clearError: () => {},
};
let recorderState: VoiceRecorderApi = idle;

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id.includes("components/calls/use-voice-recorder")) return { useVoiceRecorder: () => recorderState };
    return originalLoad.call(this, id, ...args);
};
let VoiceFeedbackRecorder: React.ComponentType<{ callId: string }>;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stub above
    VoiceFeedbackRecorder = require("./voice-feedback-recorder").VoiceFeedbackRecorder;
} finally {
    loader._load = originalLoad;
}

const originalGetCallFeedback = extendedApi.getCallFeedback;

afterEach(() => {
    cleanup();
    recorderState = idle;
    extendedApi.getCallFeedback = originalGetCallFeedback;
});

test("before anything is recorded the too-short sentence is not shown", async () => {
    extendedApi.getCallFeedback = async () => null;
    renderWithQueryClient(<VoiceFeedbackRecorder callId="call-1" />);
    await waitFor(() => {
        assert.equal(screen.queryByRole("button", { name: "Hold to record feedback" }) !== null, true);
    });
    assert.equal(screen.queryByText(TOO_SHORT) !== null, false);
});

test("after a recording attempt that was too short the sentence is shown", async () => {
    extendedApi.getCallFeedback = async () => null;
    recorderState = {
        ...idle,
        phase: "preview",
        clip: { blob: new Blob(["x"], { type: "audio/webm" }), url: "blob:too-short", mimeType: "audio/webm", seconds: 0.2 },
    };
    renderWithQueryClient(<VoiceFeedbackRecorder callId="call-1" />);
    await waitFor(() => {
        assert.equal(screen.queryByText(TOO_SHORT) !== null, true);
    });
});
