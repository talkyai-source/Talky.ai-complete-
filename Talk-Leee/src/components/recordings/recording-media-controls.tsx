"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Download, Loader2, Lock, Pause, Play, Trash2 } from "lucide-react";

import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { extendedApi, type Recording } from "@/lib/extended-api";
import { isApiClientError } from "@/lib/http-client";

const PLAYBACK_SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];

function formatDuration(seconds: number) {
    const safe = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
    const mins = Math.floor(safe / 60);
    const secs = safe % 60;
    return `${mins}:${secs.toString().padStart(2, "0")}`;
}

function normalizeReason(reason: string) {
    return reason.trim().replace(/\s+/g, " ");
}

function isValidDeleteReason(reason: string) {
    const normalized = normalizeReason(reason);
    return normalized.length >= 8 && normalized.length <= 1000 && /\p{L}/u.test(normalized);
}

function newIdempotencyKey() {
    if (typeof globalThis.crypto?.randomUUID === "function") {
        return globalThis.crypto.randomUUID();
    }
    return `recording-delete-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function friendlyDeleteError(error: unknown): Error {
    if (!isApiClientError(error)) {
        return error instanceof Error ? error : new Error("Could not delete this recording. Please try again.");
    }

    if (error.status === 423 || error.code === "recording_legal_hold") {
        return new Error("This recording is under legal hold and cannot be deleted.");
    }
    if (error.status === 409 || error.code === "idempotency_conflict") {
        return new Error("This delete request conflicts with an earlier request. Close this dialog and try again.");
    }
    if (error.status === 403 || error.code === "permission_denied") {
        return new Error("You no longer have permission to delete recordings.");
    }
    if (error.code === "authorization_unavailable") {
        return new Error("Recording permissions could not be verified. Please retry shortly.");
    }
    if (error.code === "recording_delete_storage_failed") {
        return new Error("The recording could not be removed from storage. Retry this same request.");
    }
    return error;
}

export function RecordingMediaControls({
    recording,
    canPlay,
    canDownload,
    canDelete,
    onDeleted,
}: {
    recording: Pick<Recording, "id" | "legal_hold">;
    canPlay: boolean;
    canDownload: boolean;
    canDelete: boolean;
    onDeleted: (recordingId: string) => void;
}) {
    const audioRef = useRef<HTMLAudioElement | null>(null);
    const mountedRef = useRef(false);
    const playbackUrlRef = useRef<string | null>(null);
    const playbackPromiseRef = useRef<Promise<string> | null>(null);
    const playbackAbortRef = useRef<AbortController | null>(null);
    const downloadAbortRef = useRef<AbortController | null>(null);

    const [playbackUrl, setPlaybackUrl] = useState<string | null>(null);
    const [playbackLoading, setPlaybackLoading] = useState(false);
    const [isPlaying, setIsPlaying] = useState(false);
    const [currentTime, setCurrentTime] = useState(0);
    const [duration, setDuration] = useState(0);
    const [rate, setRate] = useState(1);
    const [mediaError, setMediaError] = useState<string | null>(null);
    const [playRevoked, setPlayRevoked] = useState(false);
    const [downloadRevoked, setDownloadRevoked] = useState(false);
    const [downloading, setDownloading] = useState(false);

    const [deleteOpen, setDeleteOpen] = useState(false);
    const [deleteReason, setDeleteReason] = useState("");
    const [deleteKey, setDeleteKey] = useState("");
    const [deleteAttempted, setDeleteAttempted] = useState(false);
    const [deleteRevoked, setDeleteRevoked] = useState(false);
    const [legalHoldDetected, setLegalHoldDetected] = useState(recording.legal_hold);

    const playbackAllowed = canPlay && !playRevoked;
    const downloadAllowed = canDownload && !downloadRevoked;
    const deleteAllowed = canDelete && !deleteRevoked;
    const underLegalHold = recording.legal_hold || legalHoldDetected;

    const releasePlayback = useCallback(() => {
        audioRef.current?.pause();
        playbackAbortRef.current?.abort();
        playbackAbortRef.current = null;
        playbackPromiseRef.current = null;
        const url = playbackUrlRef.current;
        playbackUrlRef.current = null;
        if (url) URL.revokeObjectURL(url);
        if (mountedRef.current) {
            setPlaybackUrl(null);
            setIsPlaying(false);
            setCurrentTime(0);
            setDuration(0);
        }
    }, []);

    useEffect(() => {
        mountedRef.current = true;
        return () => {
            mountedRef.current = false;
            downloadAbortRef.current?.abort();
            releasePlayback();
        };
    }, [releasePlayback]);

    useEffect(() => {
        if (!playbackAllowed && (playbackUrlRef.current || playbackPromiseRef.current)) releasePlayback();
    }, [playbackAllowed, releasePlayback]);

    useEffect(() => {
        if (!downloadAllowed) downloadAbortRef.current?.abort();
    }, [downloadAllowed]);

    const ensurePlaybackUrl = useCallback(async () => {
        if (playbackUrlRef.current) return playbackUrlRef.current;
        if (playbackPromiseRef.current) return playbackPromiseRef.current;

        const controller = new AbortController();
        playbackAbortRef.current = controller;
        setPlaybackLoading(true);
        setMediaError(null);

        const request = (async () => {
            const blob = await extendedApi.fetchRecordingPlaybackBlob(recording.id, controller.signal);
            const url = URL.createObjectURL(blob);
            if (!mountedRef.current || controller.signal.aborted) {
                URL.revokeObjectURL(url);
                throw new DOMException("Request aborted", "AbortError");
            }
            playbackUrlRef.current = url;
            setPlaybackUrl(url);
            return url;
        })();
        playbackPromiseRef.current = request;

        try {
            return await request;
        } catch (error) {
            if (isApiClientError(error) && error.status === 403) setPlayRevoked(true);
            if (!controller.signal.aborted && mountedRef.current) {
                setMediaError(
                    isApiClientError(error) && error.status === 403
                        ? "You do not have permission to play this recording."
                        : "Couldn't load this recording.",
                );
            }
            throw error;
        } finally {
            if (playbackAbortRef.current === controller) playbackAbortRef.current = null;
            if (playbackPromiseRef.current === request) playbackPromiseRef.current = null;
            if (mountedRef.current) setPlaybackLoading(false);
        }
    }, [recording.id]);

    const togglePlay = useCallback(async () => {
        if (!playbackAllowed) return;
        const audio = audioRef.current;
        if (!audio) return;
        if (!audio.paused) {
            audio.pause();
            return;
        }

        try {
            const url = await ensurePlaybackUrl();
            if (!mountedRef.current || !audioRef.current) return;
            if (audioRef.current.src !== url) audioRef.current.src = url;
            audioRef.current.playbackRate = rate;
            await audioRef.current.play();
        } catch (error) {
            if (!(error instanceof DOMException && error.name === "AbortError") && mountedRef.current) {
                setMediaError((current) => current ?? "Couldn't play this recording.");
            }
        }
    }, [ensurePlaybackUrl, playbackAllowed, rate]);

    const handleDownload = useCallback(async () => {
        if (!downloadAllowed || downloading) return;
        const controller = new AbortController();
        downloadAbortRef.current?.abort();
        downloadAbortRef.current = controller;
        setDownloading(true);
        setMediaError(null);
        let url: string | null = null;
        try {
            const blob = await extendedApi.downloadRecordingBlob(recording.id, controller.signal);
            if (!mountedRef.current || controller.signal.aborted) return;
            url = URL.createObjectURL(blob);
            const anchor = document.createElement("a");
            anchor.href = url;
            anchor.download = `recording-${recording.id}.wav`;
            anchor.click();
        } catch (error) {
            if (isApiClientError(error) && error.status === 403) setDownloadRevoked(true);
            if (!controller.signal.aborted && mountedRef.current) {
                setMediaError(
                    isApiClientError(error) && error.status === 403
                        ? "You do not have permission to download this recording."
                        : "Couldn't download this recording.",
                );
            }
        } finally {
            if (url) URL.revokeObjectURL(url);
            if (downloadAbortRef.current === controller) downloadAbortRef.current = null;
            if (mountedRef.current) setDownloading(false);
        }
    }, [downloadAllowed, downloading, recording.id]);

    const openDeleteDialog = useCallback(() => {
        if (!deleteAllowed || underLegalHold) return;
        setDeleteReason("");
        setDeleteKey(newIdempotencyKey());
        setDeleteAttempted(false);
        setDeleteOpen(true);
    }, [deleteAllowed, underLegalHold]);

    const handleDelete = useCallback(async () => {
        const reason = normalizeReason(deleteReason);
        if (!isValidDeleteReason(reason) || !deleteKey) {
            throw new Error("Enter a meaningful reason of at least 8 characters.");
        }
        setDeleteAttempted(true);
        try {
            await extendedApi.deleteRecording(recording.id, {
                reason,
                idempotencyKey: deleteKey,
            });
            onDeleted(recording.id);
        } catch (error) {
            if (isApiClientError(error)) {
                if (error.status === 423 || error.code === "recording_legal_hold") {
                    setLegalHoldDetected(true);
                }
                if (error.status === 403 || error.code === "permission_denied") {
                    setDeleteRevoked(true);
                }
            }
            throw friendlyDeleteError(error);
        }
    }, [deleteKey, deleteReason, onDeleted, recording.id]);

    const setDeleteDialogOpen = useCallback((open: boolean) => {
        setDeleteOpen(open);
        if (!open) {
            setDeleteReason("");
            setDeleteKey("");
            setDeleteAttempted(false);
        }
    }, []);

    return (
        <div className="space-y-2">
            <audio
                ref={audioRef}
                hidden
                src={playbackUrl ?? undefined}
                onTimeUpdate={() => setCurrentTime(audioRef.current?.currentTime ?? 0)}
                onLoadedMetadata={() => {
                    const nextDuration = audioRef.current?.duration ?? 0;
                    setDuration(Number.isFinite(nextDuration) ? nextDuration : 0);
                }}
                onPlay={() => setIsPlaying(true)}
                onPause={() => setIsPlaying(false)}
                onEnded={() => {
                    setIsPlaying(false);
                    setCurrentTime(0);
                }}
            />

            <div className="flex flex-wrap items-center gap-3">
                {playbackAllowed ? (
                    <button
                        type="button"
                        onClick={() => void togglePlay()}
                        disabled={playbackLoading}
                        aria-label={isPlaying ? "Pause recording" : "Play recording"}
                        className="inline-flex h-9 w-9 items-center justify-center rounded-full bg-foreground/5 text-foreground transition hover:bg-foreground/10 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                        {playbackLoading ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : isPlaying ? <Pause className="h-4 w-4" aria-hidden /> : <Play className="h-4 w-4" aria-hidden />}
                    </button>
                ) : null}

                {playbackUrl ? (
                    <div className="flex min-w-[16rem] flex-1 items-center gap-2">
                        <span className="w-10 text-xs tabular-nums text-foreground/70">{formatDuration(currentTime)}</span>
                        <input
                            type="range"
                            min={0}
                            max={duration || 100}
                            value={currentTime}
                            onChange={(event) => {
                                const next = Number(event.target.value);
                                if (audioRef.current) audioRef.current.currentTime = next;
                                setCurrentTime(next);
                            }}
                            aria-label="Recording position"
                            className="h-1 flex-1 cursor-pointer appearance-none rounded-lg bg-foreground/15 accent-foreground"
                        />
                        <span className="w-10 text-xs tabular-nums text-foreground/70">{formatDuration(duration)}</span>
                        <select
                            value={rate}
                            onChange={(event) => setRate(Number(event.target.value))}
                            aria-label="Playback speed"
                            className="cursor-pointer rounded-md bg-foreground/5 px-1.5 py-1 text-xs font-medium text-foreground/80 outline-none hover:bg-foreground/10"
                        >
                            {PLAYBACK_SPEEDS.map((speed) => <option key={speed} value={speed}>{speed}×</option>)}
                        </select>
                    </div>
                ) : playbackAllowed ? (
                    <span className="flex-1 text-xs text-muted-foreground">Audio loads only when you press play.</span>
                ) : (
                    <span className="flex-1 text-xs text-muted-foreground">Playback permission is required.</span>
                )}

                {downloadAllowed ? (
                    <button
                        type="button"
                        onClick={() => void handleDownload()}
                        disabled={downloading}
                        aria-label="Download recording"
                        title="Download recording"
                        className="inline-flex h-9 w-9 items-center justify-center rounded-full bg-foreground/5 text-foreground transition hover:bg-foreground/10 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                        {downloading ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Download className="h-4 w-4" aria-hidden />}
                    </button>
                ) : null}

                {underLegalHold ? (
                    <span className="inline-flex items-center gap-1 rounded-full border border-amber-500/30 bg-amber-500/10 px-2.5 py-1 text-xs font-medium text-amber-700 dark:text-amber-300" title="Deletion is blocked while legal hold is active">
                        <Lock className="h-3.5 w-3.5" aria-hidden /> Legal hold
                    </span>
                ) : deleteAllowed ? (
                    <button
                        type="button"
                        onClick={openDeleteDialog}
                        aria-label="Delete recording"
                        title="Delete recording"
                        className="inline-flex h-9 w-9 items-center justify-center rounded-full bg-red-500/10 text-red-600 transition hover:bg-red-500/20 dark:text-red-300"
                    >
                        <Trash2 className="h-4 w-4" aria-hidden />
                    </button>
                ) : null}
            </div>

            {mediaError ? <p className="text-xs text-destructive" role="alert">{mediaError}</p> : null}

            <ConfirmDialog
                open={deleteOpen}
                onOpenChange={setDeleteDialogOpen}
                intent="delete"
                title="Delete recording permanently?"
                description="The audio will be permanently removed. This cannot be undone."
                warningText="Legal holds override deletion requests. Your reason and request identifier are retained for audit and safe retry."
                confirmLabel="Delete recording"
                pendingLabel="Deleting recording..."
                showReasonInput
                reasonValue={deleteReason}
                onReasonChange={setDeleteReason}
                reasonLabel="Reason (required)"
                reasonPlaceholder="Why must this recording be deleted?"
                reasonMaxLength={1000}
                reasonDisabled={deleteAttempted}
                confirmDisabled={!deleteAllowed || underLegalHold || !isValidDeleteReason(deleteReason)}
                onConfirm={handleDelete}
            />
        </div>
    );
}
