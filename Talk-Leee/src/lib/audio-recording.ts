/**
 * Container negotiation for browser audio capture.
 *
 * WHY THIS IS A SEPARATE, PURE MODULE
 * -----------------------------------
 * Safari could not produce WebM at all until 18.4 — its MediaRecorder emits
 * MP4/AAC. So the idiom
 *
 *     const rec = new MediaRecorder(stream);              // no mimeType
 *     new Blob(chunks, { type: "audio/webm" });           // asserted, not asked
 *
 * (which is what `components/ai-options/voice-clone-modal.tsx` does today)
 * labels an MP4 as WebM on every iPhone. The lie is unusually durable: our
 * backend accepts both containers, and Deepgram sniffs the container rather
 * than trusting the header, so the upload succeeds and the transcript is
 * correct. It fails only at *playback*, and only on the device the developer
 * does not have open. That is precisely the kind of defect that ships.
 *
 * The rule this module enforces: never assert a container, always ask the
 * browser and then report what it actually produced.
 */

/**
 * Preference order, best first.
 *
 * Opus-in-WebM is smallest for speech and is what Chrome, Firefox and Edge all
 * produce. `audio/mp4` is here for Safari below 18.4, which supports nothing
 * else. Every entry is a container the backend's `_MIME_EXTENSIONS` map accepts
 * — keep the two in step.
 */
export const FEEDBACK_MIME_CANDIDATES = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/mp4",
    "audio/ogg;codecs=opus",
    "audio/ogg",
] as const;

/**
 * Mirror of the backend's supported set (`supported_feedback_mime_types`).
 *
 * Duplicated deliberately: it lets the UI refuse an impossible recording with a
 * sentence the user can act on, instead of uploading several megabytes to earn
 * a 415. If the backend list changes, this one has to change with it — the
 * accompanying test pins the pairing.
 */
export const SUPPORTED_FEEDBACK_MIME_TYPES: readonly string[] = [
    "audio/webm",
    "video/webm",
    "audio/ogg",
    "application/ogg",
    "audio/mp4",
    "video/mp4",
    "audio/mpeg",
    "audio/wav",
    "audio/x-wav",
];

const MIME_EXTENSIONS: Readonly<Record<string, string>> = {
    "audio/webm": "webm",
    "video/webm": "webm",
    "audio/ogg": "ogg",
    "application/ogg": "ogg",
    "audio/mp4": "m4a",
    "video/mp4": "mp4",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
};

/**
 * Drop codec parameters and normalise case: `audio/webm;codecs=opus` →
 * `audio/webm`. Matches the backend's `normalized_mime_type` exactly, so what
 * the UI validates is what the server will validate.
 */
export function baseMimeType(value: string | null | undefined): string {
    return (value ?? "").split(";", 1)[0]!.trim().toLowerCase();
}

export function isSupportedFeedbackMimeType(value: string | null | undefined): boolean {
    return SUPPORTED_FEEDBACK_MIME_TYPES.includes(baseMimeType(value));
}

export function extensionForMimeType(value: string | null | undefined): string {
    return MIME_EXTENSIONS[baseMimeType(value)] ?? "webm";
}

/**
 * The best candidate this browser admits to supporting, or `undefined`.
 *
 * `undefined` means "no opinion" and must be passed to MediaRecorder as an
 * absent option rather than as a string — the constructor then picks its own
 * default, which is always something it can actually produce. Returning a
 * guess here instead would re-create the bug this module exists to prevent.
 *
 * `isSupported` is injectable so the negotiation can be tested for browsers the
 * test runner is not.
 */
export function pickRecordingMimeType(
    isSupported?: (type: string) => boolean,
): string | undefined {
    const supported =
        isSupported ??
        (typeof MediaRecorder !== "undefined" &&
        typeof MediaRecorder.isTypeSupported === "function"
            ? (type: string) => MediaRecorder.isTypeSupported(type)
            : undefined);

    // Safari before 14.1 has MediaRecorder but no isTypeSupported. It can only
    // produce MP4, and asking for anything is worse than asking for nothing.
    if (!supported) return undefined;

    for (const candidate of FEEDBACK_MIME_CANDIDATES) {
        try {
            if (supported(candidate)) return candidate;
        } catch {
            // A throwing isTypeSupported is not a reason to abandon recording.
        }
    }
    return undefined;
}

/**
 * The container that was actually produced.
 *
 * `MediaRecorder.mimeType` is populated after construction and is authoritative
 * — it reflects the browser's real choice, including when it ignored ours. Only
 * when it is empty (older Safari leaves it blank) do we fall back to what we
 * asked for, and finally to WebM.
 */
export function resolveRecordedMimeType(
    recorderMimeType: string | null | undefined,
    requested?: string,
): string {
    const actual = baseMimeType(recorderMimeType);
    if (actual) return actual;
    const asked = baseMimeType(requested);
    if (asked) return asked;
    return "audio/webm";
}

export function feedbackFileName(mimeType: string): string {
    return `feedback.${extensionForMimeType(mimeType)}`;
}

/** Human-readable reason a microphone could not be opened. */
export type MicrophoneErrorKind =
    | "denied"
    | "no-device"
    | "insecure-context"
    | "unsupported"
    | "unknown";

export interface MicrophoneError {
    kind: MicrophoneErrorKind;
    message: string;
}

/**
 * Turn a getUserMedia rejection into something worth showing a user.
 *
 * These failures have completely different remedies — grant a permission, plug
 * in a microphone, or fix the page's origin — so collapsing them into one
 * "Couldn't access the microphone" leaves the user with no next step. The
 * insecure-context case matters most: it is invisible in local development
 * (localhost is privileged) and appears only once someone opens the app over
 * plain HTTP.
 */
export function describeMicrophoneError(error: unknown): MicrophoneError {
    const name =
        typeof error === "object" && error !== null && "name" in error
            ? String((error as { name: unknown }).name)
            : "";

    if (name === "NotAllowedError" || name === "SecurityError") {
        return {
            kind: "denied",
            message:
                "Microphone access is blocked. Allow it for this site in your " +
                "browser's address-bar permissions, then try again.",
        };
    }
    if (name === "NotFoundError" || name === "OverconstrainedError") {
        return {
            kind: "no-device",
            message: "No microphone was found. Connect one and try again.",
        };
    }
    if (name === "NotReadableError") {
        return {
            kind: "unknown",
            message:
                "Your microphone is in use by another application. Close it and try again.",
        };
    }
    if (typeof window !== "undefined" && window.isSecureContext === false) {
        return {
            kind: "insecure-context",
            message:
                "Recording needs a secure connection. Open this page over HTTPS and try again.",
        };
    }
    if (typeof navigator !== "undefined" && !navigator.mediaDevices) {
        return {
            kind: "unsupported",
            message: "This browser cannot record audio. Try Chrome, Edge, Firefox or Safari.",
        };
    }
    return {
        kind: "unknown",
        message: "Couldn't start recording. Check your microphone and try again.",
    };
}

/** Backend limits, mirrored so the UI can refuse before spending an upload. */
export const FEEDBACK_MAX_BYTES = 10 * 1024 * 1024; // CALL_FEEDBACK_MAX_AUDIO_BYTES
export const FEEDBACK_MAX_SECONDS = 300; // POST /feedback: Form(..., le=300)

/**
 * Below this, the "recording" is a mis-click rather than a note. Sending it
 * would consume the call's single note slot with silence.
 */
export const FEEDBACK_MIN_SECONDS = 0.6;

export function formatDuration(seconds: number): string {
    const total = Math.max(0, Math.floor(seconds));
    const mins = Math.floor(total / 60);
    const secs = total % 60;
    return `${mins}:${secs.toString().padStart(2, "0")}`;
}
