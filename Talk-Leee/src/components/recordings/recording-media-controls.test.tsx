import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";

import { RecordingMediaControls } from "@/components/recordings/recording-media-controls";
import { extendedApi } from "@/lib/extended-api";
import { ApiClientError } from "@/lib/http-client";
import { ensureDom } from "@/test-utils/dom";
import { renderWithQueryClient } from "@/test-utils/render";

ensureDom();

const originalPlayback = extendedApi.fetchRecordingPlaybackBlob;
const originalDownload = extendedApi.downloadRecordingBlob;
const originalDelete = extendedApi.deleteRecording;
const originalCreateObjectUrl = URL.createObjectURL;
const originalRevokeObjectUrl = URL.revokeObjectURL;
const originalPlay = window.HTMLMediaElement.prototype.play;
const originalPause = window.HTMLMediaElement.prototype.pause;
const originalAnchorClick = window.HTMLAnchorElement.prototype.click;

beforeEach(() => {
    let objectUrl = 0;
    URL.createObjectURL = () => `blob:recording-test-${++objectUrl}`;
    URL.revokeObjectURL = () => {};
    window.HTMLMediaElement.prototype.play = async function play() {
        this.dispatchEvent(new Event("play"));
    };
    window.HTMLMediaElement.prototype.pause = function pause() {
        this.dispatchEvent(new Event("pause"));
    };
    window.HTMLAnchorElement.prototype.click = () => {};
});

afterEach(() => {
    cleanup();
    extendedApi.fetchRecordingPlaybackBlob = originalPlayback;
    extendedApi.downloadRecordingBlob = originalDownload;
    extendedApi.deleteRecording = originalDelete;
    URL.createObjectURL = originalCreateObjectUrl;
    URL.revokeObjectURL = originalRevokeObjectUrl;
    window.HTMLMediaElement.prototype.play = originalPlay;
    window.HTMLMediaElement.prototype.pause = originalPause;
    window.HTMLAnchorElement.prototype.click = originalAnchorClick;
});

function renderControls(overrides: Partial<React.ComponentProps<typeof RecordingMediaControls>> = {}) {
    return renderWithQueryClient(
        <RecordingMediaControls
            recording={{ id: "recording-1", legal_hold: false }}
            canPlay
            canDownload
            canDelete
            onDeleted={() => {}}
            {...overrides}
        />,
    );
}

test("playback is lazy and download uses a separate request", async () => {
    const user = userEvent.setup({ document: globalThis.document });
    let playbackCalls = 0;
    let downloadCalls = 0;
    extendedApi.fetchRecordingPlaybackBlob = async () => {
        playbackCalls += 1;
        return new Blob(["playback"], { type: "audio/wav" });
    };
    extendedApi.downloadRecordingBlob = async () => {
        downloadCalls += 1;
        return new Blob(["download"], { type: "audio/wav" });
    };

    renderControls();
    assert.equal(playbackCalls, 0);
    assert.equal(downloadCalls, 0);

    await user.click(screen.getByRole("button", { name: "Play recording" }));
    await waitFor(() => assert.equal(playbackCalls, 1));
    assert.equal(downloadCalls, 0);

    await user.click(screen.getByRole("button", { name: "Download recording" }));
    await waitFor(() => assert.equal(downloadCalls, 1));
    assert.equal(playbackCalls, 1);
});

test("recording controls fail closed for each missing permission", () => {
    renderControls({ canPlay: false, canDownload: false, canDelete: false });
    assert.equal(screen.queryByRole("button", { name: "Play recording" }), null);
    assert.equal(screen.queryByRole("button", { name: "Download recording" }), null);
    assert.equal(screen.queryByRole("button", { name: "Delete recording" }), null);
    assert.ok(screen.getByText("Playback permission is required."));
});

test("delete retries retain one key and lock the audited reason", async () => {
    const user = userEvent.setup({ document: globalThis.document });
    const requests: Array<{ reason: string; idempotencyKey: string }> = [];
    let deleted = 0;
    extendedApi.deleteRecording = async (_recordingId, input) => {
        requests.push(input);
        if (requests.length === 1) {
            throw new ApiClientError({
                status: 503,
                code: "recording_delete_storage_failed",
                message: "storage unavailable",
                url: "/recordings/recording-1",
                method: "DELETE",
            });
        }
    };

    renderControls({ onDeleted: () => { deleted += 1; } });
    await user.click(screen.getByRole("button", { name: "Delete recording" }));
    const dialog = screen.getByRole("dialog");
    const reason = within(dialog).getByLabelText("Reason (required)");
    await user.type(reason, "Customer requested permanent erasure");
    await user.click(within(dialog).getByRole("button", { name: "Delete recording" }));

    await waitFor(() => {
        assert.ok(screen.getByText("The recording could not be removed from storage. Retry this same request."));
    });
    assert.equal((reason as HTMLInputElement).disabled, true);

    await user.click(within(dialog).getByRole("button", { name: "Delete recording" }));
    await waitFor(() => assert.equal(deleted, 1));
    assert.equal(requests.length, 2);
    assert.equal(requests[0]!.reason, "Customer requested permanent erasure");
    assert.equal(requests[1]!.reason, requests[0]!.reason);
    assert.equal(requests[1]!.idempotencyKey, requests[0]!.idempotencyKey);
});

test("legal hold keeps the row and disables further deletion", async () => {
    const user = userEvent.setup({ document: globalThis.document });
    let deleted = 0;
    extendedApi.deleteRecording = async () => {
        throw new ApiClientError({
            status: 423,
            code: "recording_legal_hold",
            message: "held",
            url: "/recordings/recording-1",
            method: "DELETE",
        });
    };

    renderControls({ onDeleted: () => { deleted += 1; } });
    await user.click(screen.getByRole("button", { name: "Delete recording" }));
    const dialog = screen.getByRole("dialog");
    await user.type(within(dialog).getByLabelText("Reason (required)"), "Customer requested erasure");
    await user.click(within(dialog).getByRole("button", { name: "Delete recording" }));

    await waitFor(() => {
        assert.ok(screen.getByText("This recording is under legal hold and cannot be deleted."));
        assert.ok(screen.getByText("Legal hold"));
    });
    assert.equal(deleted, 0);
    assert.equal((within(dialog).getByRole("button", { name: "Delete recording" }) as HTMLButtonElement).disabled, true);
});
