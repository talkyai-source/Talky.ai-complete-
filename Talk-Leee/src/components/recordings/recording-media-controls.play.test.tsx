import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";

import { RecordingMediaControls } from "@/components/recordings/recording-media-controls";
import { extendedApi } from "@/lib/extended-api";
import { ensureDom } from "@/test-utils/dom";
import { renderWithQueryClient } from "@/test-utils/render";

ensureDom();

const originalPlayback = extendedApi.fetchRecordingPlaybackBlob;
const originalCreateObjectUrl = URL.createObjectURL;
const originalRevokeObjectUrl = URL.revokeObjectURL;
const originalPlay = window.HTMLMediaElement.prototype.play;
const originalPause = window.HTMLMediaElement.prototype.pause;
const originalSetAttribute = window.HTMLMediaElement.prototype.setAttribute;

// A browser restarts loading whenever the src attribute is written, even with
// the same value, and rejects a play() that is still pending with AbortError.
// JSDOM does neither, so the model is installed here.
const pendingPlays = new WeakMap<HTMLMediaElement, { interrupted: boolean }>();
let playCalls = 0;

beforeEach(() => {
    playCalls = 0;
    URL.createObjectURL = () => "blob:recording-play-test";
    URL.revokeObjectURL = () => {};
    window.HTMLMediaElement.prototype.setAttribute = function setAttribute(name: string, value: string) {
        if (name === "src") {
            const pending = pendingPlays.get(this);
            if (pending) pending.interrupted = true;
        }
        originalSetAttribute.call(this, name, value);
    };
    window.HTMLMediaElement.prototype.play = function play() {
        playCalls += 1;
        const state = { interrupted: false };
        pendingPlays.set(this, state);
        return new Promise<void>((resolve, reject) => {
            setTimeout(() => {
                pendingPlays.delete(this);
                if (state.interrupted) {
                    reject(new DOMException("The play() request was interrupted by a new load request.", "AbortError"));
                    return;
                }
                this.dispatchEvent(new Event("play"));
                resolve();
            }, 30);
        });
    };
    window.HTMLMediaElement.prototype.pause = function pause() {
        this.dispatchEvent(new Event("pause"));
    };
});

afterEach(() => {
    cleanup();
    extendedApi.fetchRecordingPlaybackBlob = originalPlayback;
    URL.createObjectURL = originalCreateObjectUrl;
    URL.revokeObjectURL = originalRevokeObjectUrl;
    window.HTMLMediaElement.prototype.play = originalPlay;
    window.HTMLMediaElement.prototype.pause = originalPause;
    window.HTMLMediaElement.prototype.setAttribute = originalSetAttribute;
});

function renderControls() {
    return renderWithQueryClient(
        <RecordingMediaControls
            recording={{ id: "recording-1", legal_hold: false }}
            canPlay
            canDownload
            canDelete
            onDeleted={() => {}}
        />,
    );
}

test("the first press of Play starts playback", async () => {
    const user = userEvent.setup({ document: globalThis.document });
    extendedApi.fetchRecordingPlaybackBlob = async () => new Blob(["playback"], { type: "audio/wav" });

    renderControls();
    await user.click(screen.getByRole("button", { name: "Play recording" }));

    await waitFor(() => {
        assert.equal(screen.queryByRole("button", { name: "Pause recording" }) !== null, true);
    });
    assert.equal(playCalls, 1);
    assert.equal(document.querySelector("audio")?.getAttribute("src"), "blob:recording-play-test");
    assert.equal(screen.queryByRole("alert") !== null, false);
});

test("a file that cannot be played shows the play error", async () => {
    const user = userEvent.setup({ document: globalThis.document });
    extendedApi.fetchRecordingPlaybackBlob = async () => new Blob(["not audio"], { type: "audio/wav" });

    renderControls();
    await user.click(screen.getByRole("button", { name: "Play recording" }));
    await waitFor(() => {
        assert.equal(screen.queryByRole("button", { name: "Pause recording" }) !== null, true);
    });

    const audio = document.querySelector("audio");
    assert.equal(audio !== null, true);
    fireEvent.error(audio!);

    await waitFor(() => {
        assert.equal(screen.queryByText("Couldn't play this recording.") !== null, true);
    });
});
