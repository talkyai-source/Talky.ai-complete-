/** Forms audit B-F23: sample size check. */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { VoiceCloneModal } from "@/components/ai-options/voice-clone-modal";

afterEach(() => cleanup());

test("B-F23: a sample over 12 MB is refused before upload with a size message", () => {
    render(<VoiceCloneModal open onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const big = new File(["x"], "big.mp3", { type: "audio/mpeg" });
    Object.defineProperty(big, "size", { value: 12 * 1024 * 1024 + 1 });
    fireEvent.change(input, { target: { files: [big] } });
    assert.ok(screen.getByText(/too large.*12 MB/));
    assert.equal(screen.queryByText("big.mp3"), null);
});
