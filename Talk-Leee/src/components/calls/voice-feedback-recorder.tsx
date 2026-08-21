"use client";

/**
 * "How did the agent do on this call?" — one voice note per call.
 *
 * The interaction is WhatsApp's, because it is the one people already know:
 * hold the mic to record, slide left to throw it away, release to review, then
 * send. A short tap locks recording hands-free so the gesture is not a trap for
 * anyone who cannot hold a pointer down.
 *
 * A saved note is durable even when its transcript is not. The backend commits
 * the audio before it calls Deepgram, so `transcript_status` can be "failed" on
 * a perfectly good recording — this component shows the player regardless and
 * treats the transcript as the optional part, never the other way round.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
    AlertCircle,
    Check,
    Loader2,
    Mic,
    Pause,
    Play,
    RefreshCw,
    Send,
    Square,
    Trash2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { extendedApi, type CallFeedback } from "@/lib/extended-api";
import {
    FEEDBACK_MAX_BYTES,
    FEEDBACK_MAX_SECONDS,
    FEEDBACK_MIN_SECONDS,
    formatDuration,
    isSupportedFeedbackMimeType,
} from "@/lib/audio-recording";
import { useVoiceRecorder } from "@/components/calls/use-voice-recorder";

/** Horizontal travel that means "cancel this". */
const CANCEL_PX = 80;
/** A press shorter than this is a tap, which locks hands-free recording. */
const TAP_MS = 400;
/**
 * How long a note may sit at "pending" before we stop pretending it is still
 * in flight. Mirrors CALL_FEEDBACK_PENDING_RETRY_AFTER_SECONDS: past this the
 * server will accept a retry, so the user gets a button instead of a spinner
 * that never resolves.
 */
const PENDING_STALE_MS = 30_000;

export function callFeedbackQueryKey(callId: string) {
    return ["callFeedback", callId] as const;
}

export function VoiceFeedbackRecorder({ callId }: { callId: string }) {
    const queryClient = useQueryClient();
    const recorder = useVoiceRecorder(FEEDBACK_MAX_SECONDS);

    const [dragX, setDragX] = useState(0);
    const [locked, setLocked] = useState(false);
    const [confirmReplace, setConfirmReplace] = useState(false);
    const [uploadError, setUploadError] = useState<string | null>(null);
    const pressedAtRef = useRef(0);
    const pressingRef = useRef(false);

    const feedbackQuery = useQuery({
        queryKey: callFeedbackQueryKey(callId),
        queryFn: () => extendedApi.getCallFeedback(callId),
        enabled: Boolean(callId),
    });
    const note = feedbackQuery.data ?? null;

    const submit = useMutation({
        mutationFn: async ({ replace }: { replace: boolean }) => {
            const clip = recorder.clip;
            if (!clip) throw new Error("Nothing recorded");
            return extendedApi.submitCallFeedback(callId, clip.blob, {
                durationSeconds: clip.seconds,
                replace,
            });
        },
        onSuccess: (saved) => {
            queryClient.setQueryData(callFeedbackQueryKey(callId), saved);
            // The call list shows a per-row indicator derived from this.
            void queryClient.invalidateQueries({ queryKey: ["calls"] });
            recorder.discard();
            setUploadError(null);
        },
        onError: (err: unknown) => {
            setUploadError(err instanceof Error ? err.message : "Couldn't send the note");
        },
    });

    const retry = useMutation({
        mutationFn: () => extendedApi.retryCallFeedbackTranscription(callId),
        onSuccess: (updated) => queryClient.setQueryData(callFeedbackQueryKey(callId), updated),
    });

    // ── gestures ────────────────────────────────────────────────────────────

    const onPointerDown = useCallback(
        (e: React.PointerEvent<HTMLButtonElement>) => {
            e.currentTarget.setPointerCapture?.(e.pointerId);
            pressedAtRef.current = Date.now();
            pressingRef.current = true;
            setDragX(0);
            setLocked(false);
            setUploadError(null);
            void recorder.start();
        },
        [recorder],
    );

    const onPointerMove = useCallback(
        (e: React.PointerEvent<HTMLButtonElement>) => {
            if (!pressingRef.current) return;
            const dx = Math.min(0, e.movementX + dragX);
            setDragX(dx);
            if (dx <= -CANCEL_PX) {
                pressingRef.current = false;
                setDragX(0);
                recorder.cancel();
            }
        },
        [dragX, recorder],
    );

    const onPointerUp = useCallback(() => {
        if (!pressingRef.current) return;
        pressingRef.current = false;
        setDragX(0);
        // A quick tap means "keep going without me holding it".
        if (Date.now() - pressedAtRef.current < TAP_MS) {
            setLocked(true);
            return;
        }
        recorder.stop();
    }, [recorder]);

    // ── transcription staleness (never an unbounded spinner) ────────────────

    const [pendingLooksStale, setPendingLooksStale] = useState(false);
    useEffect(() => {
        setPendingLooksStale(false);
        if (note?.transcript_status !== "pending") return;
        const age = Date.now() - new Date(note.updated_at).getTime();
        if (age >= PENDING_STALE_MS) {
            setPendingLooksStale(true);
            return;
        }
        const t = setTimeout(() => setPendingLooksStale(true), PENDING_STALE_MS - age);
        return () => clearTimeout(t);
    }, [note?.transcript_status, note?.updated_at]);

    const tooShort = (recorder.clip?.seconds ?? 0) < FEEDBACK_MIN_SECONDS;
    const tooBig = (recorder.clip?.blob.size ?? 0) > FEEDBACK_MAX_BYTES;
    const badContainer = Boolean(
        recorder.clip && !isSupportedFeedbackMimeType(recorder.clip.mimeType),
    );

    const blockedReason = useMemo(() => {
        if (tooShort) return "That was too short to hear. Hold the mic a little longer.";
        if (tooBig) return "That note is too large to send. Record a shorter one.";
        if (badContainer)
            return "This browser produced an audio format we can't accept. Try Chrome, Edge or Firefox.";
        return null;
    }, [badContainer, tooBig, tooShort]);

    // ── render ──────────────────────────────────────────────────────────────

    if (feedbackQuery.isLoading) {
        return (
            <Panel>
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Loader2 className="h-4 w-4 animate-spin" /> Loading feedback…
                </div>
            </Panel>
        );
    }

    // A saved note, and no new recording in progress.
    if (note && recorder.phase === "idle" && !submit.isPending) {
        return (
            <Panel>
                <SavedNote
                    callId={callId}
                    note={note}
                    stale={pendingLooksStale}
                    retrying={retry.isPending}
                    onRetry={() => retry.mutate()}
                    onRecordAgain={() => setConfirmReplace(true)}
                />
                <ConfirmDialog
                    open={confirmReplace}
                    onOpenChange={setConfirmReplace}
                    title="Replace this feedback note?"
                    description="A call keeps one note. Recording a new one supersedes the current note and its transcript."
                    warningText="The existing recording will be permanently replaced."
                    confirmLabel="Record a new note"
                    onConfirm={() => {
                        setConfirmReplace(false);
                        void recorder.start();
                    }}
                />
            </Panel>
        );
    }

    return (
        <Panel>
            <div className="mb-3 flex items-center justify-between gap-3">
                <div>
                    <h3 className="text-sm font-semibold text-foreground">
                        How did the agent do on this call?
                    </h3>
                    <p className="text-xs text-muted-foreground">
                        Leave a voice note. It&apos;s transcribed automatically.
                    </p>
                </div>
                {note && (
                    <span className="shrink-0 rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[11px] text-amber-500">
                        Replacing existing note
                    </span>
                )}
            </div>

            {recorder.phase === "recording" && (
                <div className="flex items-center gap-3 rounded-xl border border-border bg-muted/40 p-3">
                    <span className="relative flex h-3 w-3 shrink-0">
                        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-500 opacity-75" />
                        <span className="relative inline-flex h-3 w-3 rounded-full bg-red-500" />
                    </span>
                    <span className="w-12 shrink-0 font-mono text-sm tabular-nums">
                        {formatDuration(recorder.seconds)}
                    </span>
                    <Waveform levels={recorder.levels} />
                    {locked ? (
                        <Button size="sm" variant="destructive" onClick={recorder.stop}>
                            <Square className="h-3 w-3" /> Stop
                        </Button>
                    ) : (
                        <span
                            className="shrink-0 text-xs text-muted-foreground transition-opacity"
                            style={{ opacity: 1 - Math.min(1, Math.abs(dragX) / CANCEL_PX) }}
                        >
                            ‹ slide to cancel
                        </span>
                    )}
                </div>
            )}

            {recorder.phase === "preview" && recorder.clip && (
                <div className="flex flex-wrap items-center gap-3 rounded-xl border border-border bg-muted/40 p-3">
                    <ClipPlayer url={recorder.clip.url} />
                    <span className="font-mono text-sm tabular-nums">
                        {formatDuration(recorder.clip.seconds)}
                    </span>
                    <div className="ml-auto flex items-center gap-2">
                        <Button size="sm" variant="ghost" onClick={recorder.discard}>
                            <Trash2 className="h-3.5 w-3.5" /> Delete
                        </Button>
                        <Button
                            size="sm"
                            disabled={submit.isPending || Boolean(blockedReason)}
                            onClick={() => submit.mutate({ replace: Boolean(note) })}
                        >
                            {submit.isPending ? (
                                <>
                                    <Loader2 className="h-3.5 w-3.5 animate-spin" /> Sending…
                                </>
                            ) : (
                                <>
                                    <Send className="h-3.5 w-3.5" /> Send
                                </>
                            )}
                        </Button>
                    </div>
                </div>
            )}

            {(recorder.phase === "idle" || recorder.phase === "starting") && (
                <div className="flex items-center gap-3">
                    <button
                        type="button"
                        aria-label="Hold to record feedback"
                        disabled={recorder.phase === "starting"}
                        onPointerDown={onPointerDown}
                        onPointerMove={onPointerMove}
                        onPointerUp={onPointerUp}
                        onPointerCancel={onPointerUp}
                        className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground shadow transition-transform hover:scale-105 active:scale-95 disabled:opacity-50"
                    >
                        {recorder.phase === "starting" ? (
                            <Loader2 className="h-5 w-5 animate-spin" />
                        ) : (
                            <Mic className="h-5 w-5" />
                        )}
                    </button>
                    <span className="text-sm text-muted-foreground">
                        Hold to record, or tap to start hands-free.
                    </span>
                </div>
            )}

            {(recorder.error || uploadError || blockedReason) && (
                <p className="mt-3 flex items-start gap-2 text-xs text-destructive">
                    <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    <span>{recorder.error ?? uploadError ?? blockedReason}</span>
                </p>
            )}
        </Panel>
    );
}

function Panel({ children }: { children: React.ReactNode }) {
    return (
        <section className="rounded-2xl border border-border bg-background p-4 shadow-sm">
            {children}
        </section>
    );
}

/** Live loudness meter. Purely decorative — never gates a recording. */
function Waveform({ levels }: { levels: number[] }) {
    return (
        <div className="flex h-8 flex-1 items-center gap-[2px] overflow-hidden" aria-hidden="true">
            {levels.map((level, i) => (
                <span
                    key={i}
                    className="w-[3px] shrink-0 rounded-full bg-primary/70"
                    style={{ height: `${Math.round(level * 100)}%` }}
                />
            ))}
        </div>
    );
}

/** Plays a local, not-yet-uploaded clip. */
function ClipPlayer({ url }: { url: string }) {
    const ref = useRef<HTMLAudioElement | null>(null);
    const [playing, setPlaying] = useState(false);
    return (
        <>
            <audio ref={ref} src={url} onEnded={() => setPlaying(false)} className="hidden" />
            <Button
                size="icon"
                variant="outline"
                onClick={() => {
                    const el = ref.current;
                    if (!el) return;
                    if (playing) {
                        el.pause();
                        setPlaying(false);
                    } else {
                        void el.play();
                        setPlaying(true);
                    }
                }}
            >
                {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
            </Button>
        </>
    );
}

function SavedNote({
    callId,
    note,
    stale,
    retrying,
    onRetry,
    onRecordAgain,
}: {
    callId: string;
    note: CallFeedback;
    stale: boolean;
    retrying: boolean;
    onRetry: () => void;
    onRecordAgain: () => void;
}) {
    const [url, setUrl] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);
    const [playError, setPlayError] = useState<string | null>(null);
    const audioRef = useRef<HTMLAudioElement | null>(null);
    const [playing, setPlaying] = useState(false);

    useEffect(() => () => { if (url) URL.revokeObjectURL(url); }, [url]);

    const toggle = useCallback(async () => {
        const el = audioRef.current;
        if (playing && el) {
            el.pause();
            setPlaying(false);
            return;
        }
        let src = url;
        if (!src) {
            setLoading(true);
            setPlayError(null);
            try {
                // Authenticated fetch, exactly like the call recording player.
                src = await extendedApi.fetchCallFeedbackAudioBlob(callId);
                setUrl(src);
            } catch (e) {
                setPlayError(e instanceof Error ? e.message : "Couldn't load the note");
                return;
            } finally {
                setLoading(false);
            }
        }
        // The <audio> element renders once `url` is set; play on the next tick.
        requestAnimationFrame(() => {
            const node = audioRef.current;
            if (!node) return;
            void node.play();
            setPlaying(true);
        });
    }, [callId, playing, url]);

    return (
        <div>
            <div className="mb-3 flex items-center justify-between gap-3">
                <h3 className="text-sm font-semibold text-foreground">Agent feedback</h3>
                <Button size="sm" variant="ghost" onClick={onRecordAgain}>
                    <Mic className="h-3.5 w-3.5" /> Record again
                </Button>
            </div>

            <div className="flex items-center gap-3 rounded-xl border border-border bg-muted/40 p-3">
                {url && (
                    <audio
                        ref={audioRef}
                        src={url}
                        onEnded={() => setPlaying(false)}
                        className="hidden"
                    />
                )}
                <Button size="icon" variant="outline" disabled={loading} onClick={() => void toggle()}>
                    {loading ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                    ) : playing ? (
                        <Pause className="h-4 w-4" />
                    ) : (
                        <Play className="h-4 w-4" />
                    )}
                </Button>
                <span className="font-mono text-sm tabular-nums">
                    {note.duration_seconds != null ? formatDuration(note.duration_seconds) : "--:--"}
                </span>
                <span className="ml-auto text-xs text-muted-foreground">
                    {new Date(note.created_at).toLocaleString()}
                </span>
            </div>

            <TranscriptBlock note={note} stale={stale} retrying={retrying} onRetry={onRetry} />

            {playError && (
                <p className="mt-2 flex items-center gap-2 text-xs text-destructive">
                    <AlertCircle className="h-3.5 w-3.5" /> {playError}
                </p>
            )}
        </div>
    );
}

/**
 * The transcript is derived data. It gets its own visual weight precisely
 * because it is allowed to be missing — a failure here says nothing about
 * whether the recording survived.
 */
function TranscriptBlock({
    note,
    stale,
    retrying,
    onRetry,
}: {
    note: CallFeedback;
    stale: boolean;
    retrying: boolean;
    onRetry: () => void;
}) {
    if (note.transcript_status === "done" && note.transcript) {
        return (
            <blockquote className="mt-3 border-l-2 border-border pl-3 text-sm leading-relaxed text-foreground/90">
                {note.transcript}
            </blockquote>
        );
    }
    if (note.transcript_status === "done") {
        return (
            <p className="mt-3 text-xs text-muted-foreground">
                <Check className="mr-1 inline h-3 w-3" />
                Transcribed, but no speech was detected in this note.
            </p>
        );
    }
    if (note.transcript_status === "pending" && !stale) {
        return (
            <p className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Transcribing…
            </p>
        );
    }
    // Failed, or pending for long enough that nobody should still be waiting.
    return (
        <div className="mt-3 flex flex-wrap items-center gap-2">
            <p className="flex items-center gap-2 text-xs text-muted-foreground">
                <AlertCircle className="h-3.5 w-3.5 text-amber-500" />
                {note.transcript_status === "failed"
                    ? "The recording is saved, but transcription failed."
                    : "The recording is saved. Transcription didn't finish."}
            </p>
            <Button size="sm" variant="outline" disabled={retrying} onClick={onRetry}>
                {retrying ? (
                    <>
                        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Retrying…
                    </>
                ) : (
                    <>
                        <RefreshCw className="h-3.5 w-3.5" /> Retry transcription
                    </>
                )}
            </Button>
        </div>
    );
}
