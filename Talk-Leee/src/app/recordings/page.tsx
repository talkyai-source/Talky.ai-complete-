"use client";

import { useEffect, useState, useRef } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { extendedApi, Recording } from "@/lib/extended-api";
import { Play, Pause, Clock, Volume2, Download } from "lucide-react";
import { motion } from "framer-motion";

function formatDuration(seconds?: number) {
    if (!seconds) return "--:--";
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${secs.toString().padStart(2, "0")}`;
}

const PLAYBACK_SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];

function AudioPlayer({ recordingId }: { recordingId: string }) {
    const audioRef = useRef<HTMLAudioElement>(null);
    const [isPlaying, setIsPlaying] = useState(false);
    const [currentTime, setCurrentTime] = useState(0);
    const [duration, setDuration] = useState(0);
    const [blobUrl, setBlobUrl] = useState<string | null>(null);
    const [loadError, setLoadError] = useState("");
    const [downloading, setDownloading] = useState(false);
    const [rate, setRate] = useState(1);

    useEffect(() => {
        let revoke = "";
        extendedApi.fetchRecordingBlob(recordingId)
            .then((url) => {
                revoke = url;
                setBlobUrl(url);
            })
            .catch((err) => setLoadError(err.message));
        return () => {
            if (revoke) URL.revokeObjectURL(revoke);
        };
    }, [recordingId]);

    // Keep the <audio> element's playback speed in sync with the selected rate.
    // Re-applied when the source loads, because setting src resets playbackRate.
    useEffect(() => {
        if (audioRef.current) audioRef.current.playbackRate = rate;
    }, [rate, blobUrl]);

    async function togglePlay() {
        const el = audioRef.current;
        if (!el) return;
        // Drive isPlaying from the element's real play/pause events (below)
        // rather than flipping it optimistically: the old code set isPlaying
        // even when play() rejected, so the icon toggled but nothing played.
        // play() returns a promise — await + catch it so failures surface.
        if (el.paused) {
            el.playbackRate = rate;
            try {
                await el.play();
            } catch {
                setLoadError("Couldn't play this recording");
            }
        } else {
            el.pause();
        }
    }

    function handleTimeUpdate() {
        if (audioRef.current) {
            setCurrentTime(audioRef.current.currentTime);
        }
    }

    function handleLoadedMetadata() {
        if (audioRef.current) {
            setDuration(audioRef.current.duration);
        }
    }

    function handleEnded() {
        setIsPlaying(false);
        setCurrentTime(0);
    }

    function handleSeek(e: React.ChangeEvent<HTMLInputElement>) {
        const time = Number(e.target.value);
        if (audioRef.current) {
            audioRef.current.currentTime = time;
            setCurrentTime(time);
        }
    }

    async function handleDownload() {
        setDownloading(true);
        try {
            const url = blobUrl ?? await extendedApi.fetchRecordingBlob(recordingId);
            const a = document.createElement("a");
            a.href = url;
            a.download = `recording-${recordingId}.wav`;
            a.click();
        } catch {
            // ignore — blob fetch errors are already shown via loadError
        } finally {
            setDownloading(false);
        }
    }

    if (loadError) {
        return <div className="text-xs text-red-400">Failed to load audio</div>;
    }

    if (!blobUrl) {
        return <div className="text-xs text-foreground/50">Loading audio...</div>;
    }

    return (
        <div className="flex items-center gap-3">
            <audio
                ref={audioRef}
                src={blobUrl ?? undefined}
                onTimeUpdate={handleTimeUpdate}
                onLoadedMetadata={handleLoadedMetadata}
                onEnded={handleEnded}
                onPlay={() => setIsPlaying(true)}
                onPause={() => setIsPlaying(false)}
                // Surface a media-load failure (CSP block, corrupt file, network)
                // up front instead of only when the user clicks play. Guarded on
                // blobUrl so the URL.revokeObjectURL() teardown on unmount doesn't
                // flip a stale error. The blob fetch itself already succeeded by
                // the time this element renders (see the !blobUrl gate above).
                onError={() => { if (blobUrl) setLoadError("Couldn't load this recording"); }}
            />
            <button
                onClick={togglePlay}
                className="p-2 rounded-full bg-foreground/5 text-foreground transition-[transform,background-color] duration-150 ease-out hover:bg-foreground/10 hover:scale-[1.06] active:scale-[0.98]"
            >
                {isPlaying ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}
            </button>
            <div className="flex-1 flex items-center gap-2">
                <span className="text-xs text-foreground/70 w-10 tabular-nums">
                    {formatDuration(Math.floor(currentTime))}
                </span>
                <input
                    type="range"
                    min={0}
                    max={duration || 100}
                    value={currentTime}
                    onChange={handleSeek}
                    className="flex-1 h-1 bg-foreground/15 rounded-lg appearance-none cursor-pointer accent-foreground"
                />
                <span className="text-xs text-foreground/70 w-10 tabular-nums">
                    {formatDuration(Math.floor(duration))}
                </span>
            </div>
            <select
                value={rate}
                onChange={(e) => setRate(Number(e.target.value))}
                title="Playback speed"
                aria-label="Playback speed"
                className="rounded-md bg-foreground/5 px-1.5 py-1 text-xs font-medium text-foreground/80 outline-none transition-colors hover:bg-foreground/10 cursor-pointer"
            >
                {PLAYBACK_SPEEDS.map((s) => (
                    <option key={s} value={s}>{s}×</option>
                ))}
            </select>
            <button
                onClick={handleDownload}
                disabled={downloading}
                title="Download recording"
                className="p-2 rounded-full bg-foreground/5 text-foreground transition-[transform,background-color] duration-150 ease-out hover:bg-foreground/10 hover:scale-[1.06] active:scale-[0.98] disabled:opacity-50"
            >
                <Download className="w-4 h-4" />
            </button>
        </div>
    );
}

export default function RecordingsPage() {
    const [recordings, setRecordings] = useState<Recording[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [page, setPage] = useState(1);
    const [total, setTotal] = useState(0);
    const pageSize = 20;

    useEffect(() => {
        let alive = true;
        (async () => {
            try {
                setLoading(true);
                const response = await extendedApi.listRecordings(undefined, page, pageSize);
                if (!alive) return;
                setRecordings(response.items);
                setTotal(response.total);
            } catch (err) {
                if (!alive) return;
                setError(err instanceof Error ? err.message : "Failed to load recordings");
            } finally {
                if (!alive) return;
                setLoading(false);
            }
        })();
        return () => {
            alive = false;
        };
    }, [page, pageSize]);

    const totalPages = Math.ceil(total / pageSize);

    return (
        <DashboardLayout title="Recordings" description="Listen to call recordings">
            {loading ? (
                <div className="flex items-center justify-center h-64">
                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-white" />
                </div>
            ) : error ? (
                <div className="content-card border-red-500/30 text-red-400">
                    {error}
                </div>
            ) : recordings.length === 0 ? (
                <motion.div
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="content-card py-16 text-center"
                >
                    <div className="w-16 h-16 mx-auto mb-4 bg-white/10 rounded-full flex items-center justify-center">
                        <Volume2 className="w-8 h-8 text-muted-foreground" />
                    </div>
                    <h2 className="text-lg font-medium text-white mb-2">No recordings yet</h2>
                    <p className="text-muted-foreground">
                        Recordings will appear here after calls are completed.
                    </p>
                </motion.div>
            ) : (
                <>
                    <div className="space-y-4">
                        {recordings.map((recording, index) => (
                            <motion.div
                                key={recording.id}
                                initial={{ opacity: 0, x: -20 }}
                                animate={{ opacity: 1, x: 0 }}
                                transition={{ delay: index * 0.05 }}
                                whileHover={{ scale: 1.01 }}
                                whileTap={{ scale: 0.99 }}
                                className="rounded-2xl border border-border bg-muted/60 p-6 shadow-sm transition-[transform,background-color,box-shadow] duration-150 ease-out hover:bg-background hover:shadow-md"
                            >
                                <div className="flex items-center justify-between mb-3">
                                    <div className="flex items-center gap-4">
                                        <span className="text-sm font-semibold text-foreground">
                                            {recording.phone_number || `Call ${recording.call_id.slice(0, 8)}…`}
                                        </span>
                                        <span className="text-sm text-foreground/70 flex items-center gap-1">
                                            <Clock className="w-4 h-4" />
                                            {formatDuration(recording.duration_seconds)}
                                        </span>
                                    </div>
                                    <span className="text-sm text-foreground/70">
                                        {new Date(recording.created_at).toLocaleString()}
                                    </span>
                                </div>
                                <AudioPlayer recordingId={recording.id} />
                            </motion.div>
                        ))}
                    </div>

                    {/* Pagination */}
                    {totalPages > 1 && (
                        <motion.div
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            transition={{ delay: 0.3 }}
                            className="flex items-center justify-between mt-6"
                        >
                            <p className="text-sm text-muted-foreground">
                                Showing {(page - 1) * pageSize + 1} to {Math.min(page * pageSize, total)} of{" "}
                                {total} recordings
                            </p>
                            <div className="flex gap-2">
                                <button
                                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                                    disabled={page === 1}
                                    className="px-3 py-1 text-sm border border-white/20 rounded-md text-white transition-[transform,background-color,border-color] duration-150 ease-out hover:bg-white/10 hover:border-white/30 hover:scale-[1.03] active:scale-[0.98] disabled:opacity-50"
                                >
                                    Previous
                                </button>
                                <button
                                    onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                                    disabled={page === totalPages}
                                    className="px-3 py-1 text-sm border border-white/20 rounded-md text-white transition-[transform,background-color,border-color] duration-150 ease-out hover:bg-white/10 hover:border-white/30 hover:scale-[1.03] active:scale-[0.98] disabled:opacity-50"
                                >
                                    Next
                                </button>
                            </div>
                        </motion.div>
                    )}
                </>
            )}
        </DashboardLayout>
    );
}
