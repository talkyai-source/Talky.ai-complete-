"use client";

/**
 * Microphone capture for a short voice note.
 *
 * Kept apart from the UI on purpose: the media lifecycle (permissions, device
 * loss, container negotiation, releasing the mic) and the gesture layer
 * (hold, slide-to-cancel, lock) fail in unrelated ways, and debugging them
 * together means debugging neither.
 *
 * The one rule this hook will not bend: the microphone is released on every
 * exit path, including unmount mid-recording. A page that leaves the browser's
 * recording indicator lit after you navigate away reads as spyware, and the
 * user is right to think so.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import {
    FEEDBACK_MAX_SECONDS,
    describeMicrophoneError,
    pickRecordingMimeType,
    resolveRecordedMimeType,
} from "@/lib/audio-recording";

export type RecorderPhase = "idle" | "starting" | "recording" | "preview";

export interface RecordedClip {
    blob: Blob;
    /** Object URL for local playback. Revoked when the clip is discarded. */
    url: string;
    /** The container the browser actually produced, not the one we asked for. */
    mimeType: string;
    seconds: number;
}

/** Number of bars in the live meter. */
const LEVEL_BARS = 32;
const LEVEL_INTERVAL_MS = 80;

export interface VoiceRecorderApi {
    phase: RecorderPhase;
    clip: RecordedClip | null;
    /** Recent loudness, 0..1, oldest first. Empty unless recording. */
    levels: number[];
    seconds: number;
    error: string | null;
    start: () => Promise<void>;
    /** Finish and keep the audio. */
    stop: () => void;
    /** Finish and throw the audio away. */
    cancel: () => void;
    /** Drop a previewed clip and return to idle. */
    discard: () => void;
    clearError: () => void;
}

export function useVoiceRecorder(maxSeconds: number = FEEDBACK_MAX_SECONDS): VoiceRecorderApi {
    const [phase, setPhase] = useState<RecorderPhase>("idle");
    const [clip, setClip] = useState<RecordedClip | null>(null);
    const [levels, setLevels] = useState<number[]>([]);
    const [seconds, setSeconds] = useState(0);
    const [error, setError] = useState<string | null>(null);

    const recorderRef = useRef<MediaRecorder | null>(null);
    const streamRef = useRef<MediaStream | null>(null);
    const audioCtxRef = useRef<AudioContext | null>(null);
    const chunksRef = useRef<BlobPart[]>([]);
    const startedAtRef = useRef<number>(0);
    const abandonRef = useRef(false);
    const timersRef = useRef<Array<ReturnType<typeof setInterval>>>([]);
    // Read by the meter loop, which must not re-subscribe on every render.
    const requestedMimeRef = useRef<string | undefined>(undefined);

    /** Tear down every audio resource. Safe to call repeatedly. */
    const releaseHardware = useCallback(() => {
        timersRef.current.forEach(clearInterval);
        timersRef.current = [];
        try {
            streamRef.current?.getTracks().forEach((t) => t.stop());
        } catch {
            // Already-stopped tracks throw on some browsers; nothing to do.
        }
        streamRef.current = null;
        const ctx = audioCtxRef.current;
        audioCtxRef.current = null;
        if (ctx && ctx.state !== "closed") void ctx.close().catch(() => {});
        recorderRef.current = null;
    }, []);

    // The load-bearing cleanup. Without this, unmounting while recording leaves
    // the microphone open for the lifetime of the tab.
    useEffect(() => releaseHardware, [releaseHardware]);

    // Revoke the preview URL when the clip is replaced or the component dies.
    useEffect(() => {
        const url = clip?.url;
        return () => {
            if (url) URL.revokeObjectURL(url);
        };
    }, [clip?.url]);

    const finish = useCallback(
        (keep: boolean) => {
            const rec = recorderRef.current;
            if (!rec || rec.state === "inactive") {
                abandonRef.current = !keep;
                releaseHardware();
                setPhase(keep ? "preview" : "idle");
                return;
            }
            abandonRef.current = !keep;
            try {
                rec.stop(); // fires ondataavailable, then onstop
            } catch {
                releaseHardware();
                setPhase("idle");
            }
        },
        [releaseHardware],
    );

    const start = useCallback(async () => {
        if (phase === "recording" || phase === "starting") return;
        setError(null);
        setPhase("starting");
        setSeconds(0);
        setLevels([]);
        abandonRef.current = false;
        chunksRef.current = [];

        let stream: MediaStream;
        try {
            stream = await navigator.mediaDevices.getUserMedia({
                audio: { echoCancellation: true, noiseSuppression: true },
            });
        } catch (err) {
            setError(describeMicrophoneError(err).message);
            setPhase("idle");
            return;
        }
        streamRef.current = stream;

        // Ask what this browser can produce; never assert it. `undefined` means
        // "no preference" and must be omitted, not stringified.
        const requested = pickRecordingMimeType();
        requestedMimeRef.current = requested;
        let rec: MediaRecorder;
        try {
            rec = requested
                ? new MediaRecorder(stream, { mimeType: requested })
                : new MediaRecorder(stream);
        } catch {
            // A browser that rejects its own advertised type still deserves a
            // recording — fall back to its default rather than giving up.
            try {
                rec = new MediaRecorder(stream);
                requestedMimeRef.current = undefined;
            } catch (err) {
                releaseHardware();
                setError(describeMicrophoneError(err).message);
                setPhase("idle");
                return;
            }
        }

        rec.ondataavailable = (e) => {
            if (e.data && e.data.size > 0) chunksRef.current.push(e.data);
        };
        rec.onstop = () => {
            const elapsed = (Date.now() - startedAtRef.current) / 1000;
            const mimeType = resolveRecordedMimeType(rec.mimeType, requestedMimeRef.current);
            const parts = chunksRef.current;
            chunksRef.current = [];
            releaseHardware();

            if (abandonRef.current || parts.length === 0) {
                setPhase("idle");
                setLevels([]);
                setSeconds(0);
                return;
            }
            const blob = new Blob(parts, { type: mimeType });
            setClip({ blob, url: URL.createObjectURL(blob), mimeType, seconds: elapsed });
            setPhase("preview");
            setLevels([]);
        };

        // Live meter. An AnalyserNode is cheap and, unlike decoding the blob
        // afterwards, it works while the user is still speaking.
        try {
            const Ctor =
                window.AudioContext ??
                (window as unknown as { webkitAudioContext?: typeof AudioContext })
                    .webkitAudioContext;
            if (Ctor) {
                const ctx = new Ctor();
                audioCtxRef.current = ctx;
                const analyser = ctx.createAnalyser();
                analyser.fftSize = 512;
                ctx.createMediaStreamSource(stream).connect(analyser);
                const buf = new Uint8Array(analyser.fftSize);
                timersRef.current.push(
                    setInterval(() => {
                        analyser.getByteTimeDomainData(buf);
                        let sum = 0;
                        for (let i = 0; i < buf.length; i++) {
                            const v = (buf[i]! - 128) / 128;
                            sum += v * v;
                        }
                        const rms = Math.sqrt(sum / buf.length);
                        // Speech sits low in a linear scale; lift it so the bar
                        // chart reads as speech rather than as a flat line.
                        const level = Math.min(1, Math.max(0.04, rms * 3.2));
                        setLevels((prev) => [...prev, level].slice(-LEVEL_BARS));
                    }, LEVEL_INTERVAL_MS),
                );
            }
        } catch {
            // No meter is a cosmetic loss. Recording continues.
        }

        startedAtRef.current = Date.now();
        timersRef.current.push(
            setInterval(() => {
                const elapsed = (Date.now() - startedAtRef.current) / 1000;
                setSeconds(elapsed);
                // Stop at the cap rather than letting the upload be rejected
                // after the user has already spoken past it.
                if (elapsed >= maxSeconds) finish(true);
            }, 200),
        );

        try {
            rec.start();
        } catch (err) {
            releaseHardware();
            setError(describeMicrophoneError(err).message);
            setPhase("idle");
            return;
        }
        recorderRef.current = rec;
        setPhase("recording");
    }, [finish, maxSeconds, phase, releaseHardware]);

    const discard = useCallback(() => {
        setClip(null);
        setPhase("idle");
        setSeconds(0);
        setLevels([]);
    }, []);

    return {
        phase,
        clip,
        levels,
        seconds,
        error,
        start,
        stop: useCallback(() => finish(true), [finish]),
        cancel: useCallback(() => finish(false), [finish]),
        discard,
        clearError: useCallback(() => setError(null), []),
    };
}
