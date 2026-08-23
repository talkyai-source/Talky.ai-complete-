"use client";

/**
 * The three ways to answer a recording, sitting on the recording itself.
 *
 *     [thumb up] [thumb down] [mic] [text]
 *
 * All three exist because they cost different amounts and carry different
 * amounts of information, and forcing one shape makes people use none:
 *
 *   - a thumb is one click and says only better/worse — but it is the one
 *     people will actually give while working down a list;
 *   - a voice note is the fastest way to say something NUANCED ("she'd already
 *     said no twice and it kept pitching"), which is exactly the feedback that
 *     never gets typed;
 *   - typed text is the only option when you are somewhere you cannot talk.
 *
 * WHERE EACH ONE LANDS
 * --------------------
 * Thumb and text both write `conversation_reviews` — rating and comment on the
 * same row, one review per user per call. The voice note writes `call_feedback`,
 * which is a different thing: one note per CALL, stored durably and transcribed
 * afterwards. They are not merged because a voice note is a recording with a
 * lifecycle (upload, transcribe, retry) and a review is a structured judgement.
 *
 * NOTHING HERE OVERWRITES ANYTHING ELSE
 * --------------------------------------
 * `submitReview` is a PUT of the whole review, so saving a comment resends the
 * existing rating and tags untouched, and a thumb resends the existing comment.
 * Without that, whichever control you used last would silently erase the other.
 */
import { useCallback, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Loader2, MessageSquareText, Mic, Send, Square, Trash2, X } from "lucide-react";

import { extendedApi, type ConversationReview } from "@/lib/extended-api";
import { FEEDBACK_MAX_SECONDS, FEEDBACK_MIN_SECONDS } from "@/lib/audio-recording";
import { useVoiceRecorder } from "./use-voice-recorder";
import { reviewQueryKey } from "./conversation-review-panel";
import { callFeedbackQueryKey } from "./voice-feedback-recorder";
import { QuickReviewButtons } from "./quick-review-buttons";

function mmss(total: number) {
    const s = Math.max(0, Math.floor(total));
    return `0:${String(s).padStart(2, "0")}`;
}

const ICON_BTN =
    "inline-flex h-8 w-8 items-center justify-center rounded-lg border transition-colors disabled:opacity-50 disabled:cursor-not-allowed";
const IDLE =
    "border-border bg-background text-muted-foreground hover:bg-accent hover:text-foreground";

export function RecordingFeedbackBar({
    callId,
    className = "",
}: {
    callId: string;
    className?: string;
}) {
    const queryClient = useQueryClient();
    const [mode, setMode] = useState<"none" | "voice" | "text">("none");
    const [text, setText] = useState("");
    const [seededFor, setSeededFor] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [savedFlash, setSavedFlash] = useState(false);
    const flashTimer = useRef<number | null>(null);

    const recorder = useVoiceRecorder(FEEDBACK_MAX_SECONDS);

    const review = useQuery({
        queryKey: reviewQueryKey(callId),
        queryFn: () => extendedApi.getMyReview(callId),
        enabled: Boolean(callId),
        staleTime: 30_000,
    });
    const note = useQuery({
        queryKey: callFeedbackQueryKey(callId),
        queryFn: () => extendedApi.getCallFeedback(callId),
        enabled: Boolean(callId) && mode === "voice",
    });

    const existing = review.data ?? null;
    const hasNote = Boolean(note.data);

    const flashSaved = useCallback(() => {
        setSavedFlash(true);
        if (flashTimer.current) window.clearTimeout(flashTimer.current);
        flashTimer.current = window.setTimeout(() => setSavedFlash(false), 2000);
    }, []);

    // ── text → conversation_reviews.comment ─────────────────────────────────
    const saveText = useMutation({
        mutationFn: (comment: string) =>
            extendedApi.submitReview(callId, {
                // Keep whatever rating/tags already exist. A comment must not
                // reset someone's thumb to nothing.
                rating: existing?.rating ?? 3,
                tags: existing?.tags ?? [],
                comment: comment.trim() || null,
            }),
        onSuccess: (saved: ConversationReview) => {
            queryClient.setQueryData(reviewQueryKey(callId), saved);
            void queryClient.invalidateQueries({ queryKey: ["reviewList"] });
            void queryClient.invalidateQueries({ queryKey: ["reviewSummary"] });
            setError(null);
            setMode("none");
            flashSaved();
        },
        onError: (err: unknown) =>
            setError(err instanceof Error ? err.message : "Couldn't save your note"),
    });

    // ── voice → call_feedback ───────────────────────────────────────────────
    const sendVoice = useMutation({
        mutationFn: async (replace: boolean) => {
            const clip = recorder.clip;
            if (!clip) throw new Error("Nothing recorded");
            return extendedApi.submitCallFeedback(callId, clip.blob, {
                durationSeconds: clip.seconds,
                replace,
            });
        },
        onSuccess: (saved) => {
            queryClient.setQueryData(callFeedbackQueryKey(callId), saved);
            void queryClient.invalidateQueries({ queryKey: ["calls"] });
            recorder.discard();
            setError(null);
            setMode("none");
            flashSaved();
        },
        onError: (err: unknown) =>
            setError(err instanceof Error ? err.message : "Couldn't send the note"),
    });

    const openText = useCallback(() => {
        setError(null);
        // Seed once per review so re-opening shows what you wrote, without
        // stomping on in-progress typing when react-query refetches.
        if (seededFor !== (existing?.id ?? "new")) {
            setText(existing?.comment ?? "");
            setSeededFor(existing?.id ?? "new");
        }
        setMode((m) => (m === "text" ? "none" : "text"));
    }, [existing?.comment, existing?.id, seededFor]);

    const openVoice = useCallback(() => {
        setError(null);
        setMode((m) => {
            if (m === "voice") {
                recorder.cancel();
                return "none";
            }
            return "voice";
        });
    }, [recorder]);

    const clip = recorder.clip;
    const recording = recorder.phase === "recording";
    const remaining = FEEDBACK_MAX_SECONDS - recorder.seconds;
    const tooShort = Boolean(clip && clip.seconds < FEEDBACK_MIN_SECONDS);

    return (
        <div className={`flex flex-col gap-2 ${className}`}>
            <div className="flex items-center gap-1">
                <QuickReviewButtons callId={callId} />

                <button
                    type="button"
                    onClick={openVoice}
                    aria-pressed={mode === "voice"}
                    aria-label={hasNote ? "Voice note left — record a new one" : "Record a voice note"}
                    title={hasNote ? "Voice note left — record a new one" : `Record a voice note (max ${FEEDBACK_MAX_SECONDS}s)`}
                    className={`${ICON_BTN} ${mode === "voice" || hasNote
                        ? "border-primary/50 bg-primary/10 text-primary"
                        : IDLE
                        }`}
                >
                    <Mic className="h-4 w-4" />
                </button>

                <button
                    type="button"
                    onClick={openText}
                    aria-pressed={mode === "text"}
                    aria-label={existing?.comment ? "Edit your written feedback" : "Write feedback"}
                    title={existing?.comment ? "Edit your written feedback" : "Write feedback"}
                    className={`${ICON_BTN} ${mode === "text" || existing?.comment
                        ? "border-primary/50 bg-primary/10 text-primary"
                        : IDLE
                        }`}
                >
                    <MessageSquareText className="h-4 w-4" />
                </button>

                {savedFlash && (
                    <span
                        role="status"
                        className="inline-flex items-center gap-1 text-xs font-medium text-emerald-600 dark:text-emerald-400"
                    >
                        <Check className="h-3.5 w-3.5" /> Saved
                    </span>
                )}
            </div>

            {/* ── voice ───────────────────────────────────────────────────── */}
            {mode === "voice" && (
                <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-muted/40 px-3 py-2">
                    {!clip && !recording && (
                        <>
                            <button
                                type="button"
                                onClick={() => { setError(null); void recorder.start(); }}
                                className="inline-flex items-center gap-2 rounded-full bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground hover:opacity-90"
                            >
                                <Mic className="h-3.5 w-3.5" /> Record
                            </button>
                            <span className="text-xs text-muted-foreground">
                                Up to {FEEDBACK_MAX_SECONDS}s — it stops on its own.
                            </span>
                        </>
                    )}

                    {recording && (
                        <>
                            <span className="inline-flex items-center gap-2 text-xs font-semibold text-red-600 dark:text-red-400">
                                <span className="h-2 w-2 animate-pulse rounded-full bg-red-500" />
                                {mmss(recorder.seconds)} / {mmss(FEEDBACK_MAX_SECONDS)}
                            </span>
                            {remaining <= 5 && (
                                <span className="text-xs font-medium text-amber-600 dark:text-amber-400">
                                    {Math.max(0, Math.ceil(remaining))}s left
                                </span>
                            )}
                            <button
                                type="button"
                                onClick={() => recorder.stop()}
                                className="inline-flex items-center gap-1.5 rounded-full bg-foreground px-3 py-1.5 text-xs font-semibold text-background hover:opacity-90"
                            >
                                <Square className="h-3 w-3" /> Stop
                            </button>
                            <button
                                type="button"
                                onClick={() => { recorder.cancel(); setMode("none"); }}
                                aria-label="Cancel recording"
                                className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                            >
                                <X className="h-3.5 w-3.5" /> Cancel
                            </button>
                        </>
                    )}

                    {clip && (
                        <>
                            <audio src={clip.url} controls className="h-8 max-w-[220px]" />
                            <span className="text-xs text-muted-foreground tabular-nums">
                                {mmss(clip.seconds)}
                            </span>
                            <button
                                type="button"
                                disabled={sendVoice.isPending || tooShort}
                                onClick={() => sendVoice.mutate(hasNote)}
                                title={
                                    tooShort
                                        ? "Too short to send"
                                        : hasNote
                                            ? "Replace the existing note on this call"
                                            : "Send this note"
                                }
                                className="inline-flex items-center gap-1.5 rounded-full bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground hover:opacity-90 disabled:opacity-50"
                            >
                                {sendVoice.isPending ? (
                                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                ) : (
                                    <Send className="h-3.5 w-3.5" />
                                )}
                                {hasNote ? "Replace" : "Send"}
                            </button>
                            <button
                                type="button"
                                onClick={() => recorder.discard()}
                                aria-label="Discard recording"
                                className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                            >
                                <Trash2 className="h-3.5 w-3.5" /> Discard
                            </button>
                            {tooShort && (
                                <span className="text-xs text-amber-600 dark:text-amber-400">
                                    Too short — hold on a moment longer.
                                </span>
                            )}
                            {hasNote && (
                                <span className="text-xs text-muted-foreground">
                                    This call already has a note; sending replaces it.
                                </span>
                            )}
                        </>
                    )}
                </div>
            )}

            {/* ── text ────────────────────────────────────────────────────── */}
            {mode === "text" && (
                <div className="flex flex-col gap-2 rounded-lg border border-border bg-muted/40 px-3 py-2">
                    <label className="sr-only" htmlFor={`fb-text-${callId}`}>
                        Written feedback about this call
                    </label>
                    <textarea
                        id={`fb-text-${callId}`}
                        value={text}
                        onChange={(e) => setText(e.target.value)}
                        rows={2}
                        maxLength={2000}
                        placeholder="What did the agent get right or wrong?"
                        className="w-full resize-y rounded-md border border-border bg-background px-2 py-1.5 text-sm text-foreground outline-none focus:border-ring"
                    />
                    <div className="flex items-center gap-2">
                        <button
                            type="button"
                            disabled={saveText.isPending}
                            onClick={() => saveText.mutate(text)}
                            className="inline-flex items-center gap-1.5 rounded-full bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground hover:opacity-90 disabled:opacity-50"
                        >
                            {saveText.isPending ? (
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            ) : (
                                <Check className="h-3.5 w-3.5" />
                            )}
                            Save
                        </button>
                        <button
                            type="button"
                            onClick={() => setMode("none")}
                            className="text-xs text-muted-foreground hover:text-foreground"
                        >
                            Cancel
                        </button>
                        <span className="ml-auto text-xs text-muted-foreground tabular-nums">
                            {text.length}/2000
                        </span>
                    </div>
                </div>
            )}

            {(error || recorder.error) && (
                <p role="alert" className="text-xs text-red-600 dark:text-red-400">
                    {error ?? recorder.error}
                </p>
            )}
        </div>
    );
}
