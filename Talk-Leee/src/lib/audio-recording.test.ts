/**
 * The browser-compatibility bug this module exists to prevent.
 *
 * Safari could not produce WebM until 18.4 — its MediaRecorder emits MP4/AAC.
 * The failure mode is nasty because it is silent everywhere except playback on
 * an iPhone: the backend accepts both containers and Deepgram sniffs rather
 * than trusts the declared type, so an MP4 mislabelled as WebM uploads fine and
 * transcribes correctly. Nothing goes red. It just won't play back.
 *
 * A developer on Chrome cannot reproduce any of that, which is exactly why the
 * negotiation is a pure function with an injectable `isTypeSupported` — the
 * browsers we cannot run are the ones worth testing.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
    FEEDBACK_MAX_SECONDS,
    FEEDBACK_MIN_SECONDS,
    SUPPORTED_FEEDBACK_MIME_TYPES,
    baseMimeType,
    describeMicrophoneError,
    extensionForMimeType,
    feedbackFileName,
    formatDuration,
    isSupportedFeedbackMimeType,
    pickRecordingMimeType,
    resolveRecordedMimeType,
} from "@/lib/audio-recording";

// ── container negotiation ───────────────────────────────────────────────────

test("Chrome and Firefox get Opus in WebM", () => {
    const chrome = (t: string) => t.startsWith("audio/webm");
    assert.equal(pickRecordingMimeType(chrome), "audio/webm;codecs=opus");
});

test("Safari below 18.4 gets MP4, never WebM", () => {
    // The real behaviour: isTypeSupported returns false for every WebM variant.
    const oldSafari = (t: string) => t.startsWith("audio/mp4");
    assert.equal(pickRecordingMimeType(oldSafari), "audio/mp4");
});

test("Safari 18.4+ can do WebM and is given the better container", () => {
    const newSafari = (t: string) => t.startsWith("audio/webm") || t.startsWith("audio/mp4");
    assert.equal(pickRecordingMimeType(newSafari), "audio/webm;codecs=opus");
});

test("a browser that supports nothing we asked about yields no preference", () => {
    assert.equal(
        pickRecordingMimeType(() => false),
        undefined,
        "must be undefined so MediaRecorder is constructed without a mimeType " +
            "option; passing a guess is what mislabels the blob",
    );
});

test("a throwing isTypeSupported does not abort the search", () => {
    const flaky = (t: string) => {
        if (t.includes("webm")) throw new Error("nope");
        return t === "audio/mp4";
    };
    assert.equal(pickRecordingMimeType(flaky), "audio/mp4");
});

test("every candidate we would request is one the backend accepts", () => {
    // Guards the split-brain where the UI negotiates a container the server
    // answers 415 for — the upload would fail only on the affected browser.
    for (const candidate of ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus", "audio/ogg"]) {
        assert.ok(
            isSupportedFeedbackMimeType(candidate),
            `${candidate} is offered to MediaRecorder but not accepted by the API`,
        );
    }
});

// ── reporting what was actually produced ────────────────────────────────────

test("the recorder's own mimeType wins over what we requested", () => {
    // THE BUG. We ask for WebM; Safari hands back MP4 and says so. Believing our
    // own request here is precisely how the blob gets mislabelled.
    assert.equal(
        resolveRecordedMimeType("audio/mp4", "audio/webm;codecs=opus"),
        "audio/mp4",
    );
});

test("codec parameters are stripped, matching the backend's normalisation", () => {
    assert.equal(resolveRecordedMimeType("audio/webm;codecs=opus"), "audio/webm");
    assert.equal(baseMimeType("AUDIO/WebM; codecs=opus"), "audio/webm");
    assert.equal(baseMimeType(null), "");
});

test("a blank recorder mimeType falls back to the request, then to WebM", () => {
    // Older Safari leaves MediaRecorder.mimeType empty.
    assert.equal(resolveRecordedMimeType("", "audio/mp4"), "audio/mp4");
    assert.equal(resolveRecordedMimeType(undefined, undefined), "audio/webm");
});

test("filenames carry an extension that agrees with the container", () => {
    assert.equal(feedbackFileName("audio/mp4"), "feedback.m4a");
    assert.equal(feedbackFileName("audio/webm;codecs=opus"), "feedback.webm");
    assert.equal(extensionForMimeType("audio/ogg"), "ogg");
    assert.equal(extensionForMimeType("something/unknown"), "webm");
});

// ── microphone failures are not interchangeable ─────────────────────────────

test("each microphone failure gets its own remedy", () => {
    // These have different fixes — grant a permission, plug in a device, change
    // the origin. One generic message leaves the user with no next step.
    assert.equal(describeMicrophoneError({ name: "NotAllowedError" }).kind, "denied");
    assert.equal(describeMicrophoneError({ name: "NotFoundError" }).kind, "no-device");
    assert.equal(describeMicrophoneError({ name: "OverconstrainedError" }).kind, "no-device");
    assert.match(describeMicrophoneError({ name: "NotReadableError" }).message, /another application/i);
});

test("a named error always beats environment sniffing", () => {
    // The ordering that matters. A denied permission on an insecure origin, or
    // in a runtime with no navigator.mediaDevices, must still read as "denied"
    // — telling someone to switch browsers when they only need to click Allow
    // sends them down a road with no fix at the end of it.
    //
    // (This test process is one such runtime: Node exposes `navigator` but not
    // `navigator.mediaDevices`, so the environment branches are live here.)
    assert.equal(describeMicrophoneError({ name: "NotAllowedError" }).kind, "denied");
    assert.equal(describeMicrophoneError({ name: "SecurityError" }).kind, "denied");
});

test("an unrecognised failure still yields something actionable", () => {
    // Without a name to go on the classifier falls back to what it can observe
    // about the environment, so the exact `kind` depends on the runtime. What
    // must hold everywhere is that the user is told to do something.
    const { kind, message } = describeMicrophoneError(new Error("boom"));
    assert.ok(
        ["unknown", "unsupported", "insecure-context"].includes(kind),
        `unexpected fallback kind: ${kind}`,
    );
    assert.ok(message.length > 20);
});

test("every microphone message tells the user what to do next", () => {
    for (const name of ["NotAllowedError", "NotFoundError", "NotReadableError", "SecurityError"]) {
        const { message } = describeMicrophoneError({ name });
        assert.ok(message.length > 20, `${name} message is too terse to act on`);
        assert.match(message, /try again|connect|allow/i);
    }
});

// ── limits mirrored from the backend ────────────────────────────────────────

test("the duration cap is 30s and matches the API's Form(le=30)", () => {
    // Both sides must move together. The recorder stops itself at this value,
    // so if the constant drifts ABOVE the server's limit the user records a
    // note, watches it upload, and only then gets a 422 — having lost the take.
    assert.equal(FEEDBACK_MAX_SECONDS, 30);
});

test("the minimum length rejects a mis-click but not a short sentence", () => {
    assert.ok(FEEDBACK_MIN_SECONDS > 0 && FEEDBACK_MIN_SECONDS < 1.5);
});

test("the mirrored accept-list matches the backend's _MIME_EXTENSIONS", () => {
    // Kept in step by hand; this is the thing that notices when they drift.
    assert.deepEqual([...SUPPORTED_FEEDBACK_MIME_TYPES].sort(), [
        "application/ogg",
        "audio/mp4",
        "audio/mpeg",
        "audio/ogg",
        "audio/wav",
        "audio/webm",
        "audio/x-wav",
        "video/mp4",
        "video/webm",
    ]);
});

test("durations render as m:ss", () => {
    assert.equal(formatDuration(0), "0:00");
    assert.equal(formatDuration(4.9), "0:04");
    assert.equal(formatDuration(65), "1:05");
    assert.equal(formatDuration(-3), "0:00");
});
