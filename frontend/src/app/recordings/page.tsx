"use client";

import { useEffect, useState, useRef } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { extendedApi, Recording } from "@/lib/extended-api";
import { Play, Pause, Clock, Volume2 } from "lucide-react";
import { motion } from "framer-motion";

function formatDuration(seconds?: number) {
    if (!seconds) return "--:--";
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${secs.toString().padStart(2, "0")}`;
}

function AudioPlayer({ recordingId }: { recordingId: string }) {
    const audioRef = useRef<HTMLAudioElement>(null);
    const [isPlaying, setIsPlaying] = useState(false);
    const [currentTime, setCurrentTime] = useState(0);
    const [duration, setDuration] = useState(0);

    const streamUrl = extendedApi.getRecordingStreamUrl(recordingId);

    function togglePlay() {
        if (audioRef.current) {
            if (isPlaying) {
                audioRef.current.pause();
            } else {
                audioRef.current.play();
            }
            setIsPlaying(!isPlaying);
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

    return (
        <div className="flex items-center gap-3">
            <audio
                ref={audioRef}
                src={streamUrl}
                onTimeUpdate={handleTimeUpdate}
                onLoadedMetadata={handleLoadedMetadata}
                onEnded={handleEnded}
            />
            <button
                onClick={togglePlay}
                className="p-2 rounded-full bg-white text-gray-900 hover:bg-gray-200 transition-colors"
            >
                {isPlaying ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}
            </button>
            <div className="flex-1 flex items-center gap-2">
                <span className="text-xs text-gray-400 w-10">
                    {formatDuration(Math.floor(currentTime))}
                </span>
                <input
                    type="range"
                    min={0}
                    max={duration || 100}
                    value={currentTime}
                    onChange={handleSeek}
                    className="flex-1 h-1 bg-white/20 rounded-lg appearance-none cursor-pointer accent-white"
                />
                <span className="text-xs text-gray-400 w-10">
                    {formatDuration(Math.floor(duration))}
                </span>
            </div>
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
        loadRecordings();
    }, [page]);

    async function loadRecordings() {
        try {
            setLoading(true);
            const response = await extendedApi.listRecordings(undefined, page, pageSize);
            setRecordings(response.items);
            setTotal(response.total);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load recordings");
        } finally {
            setLoading(false);
        }
    }

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
                        <Volume2 className="w-8 h-8 text-gray-400" />
                    </div>
                    <h3 className="text-lg font-medium text-white mb-2">No recordings yet</h3>
                    <p className="text-gray-400">
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
                                className="content-card"
                            >
                                <div className="flex items-center justify-between mb-3">
                                    <div className="flex items-center gap-4">
                                        <span className="text-sm font-medium text-white">
                                            Call {recording.call_id.slice(0, 8)}...
                                        </span>
                                        <span className="text-sm text-gray-400 flex items-center gap-1">
                                            <Clock className="w-4 h-4" />
                                            {formatDuration(recording.duration_seconds)}
                                        </span>
                                    </div>
                                    <span className="text-sm text-gray-500">
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
                            <p className="text-sm text-gray-400">
                                Showing {(page - 1) * pageSize + 1} to {Math.min(page * pageSize, total)} of{" "}
                                {total} recordings
                            </p>
                            <div className="flex gap-2">
                                <button
                                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                                    disabled={page === 1}
                                    className="px-3 py-1 text-sm border border-white/20 rounded-md text-white hover:bg-white/10 disabled:opacity-50 transition-colors"
                                >
                                    Previous
                                </button>
                                <button
                                    onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                                    disabled={page === totalPages}
                                    className="px-3 py-1 text-sm border border-white/20 rounded-md text-white hover:bg-white/10 disabled:opacity-50 transition-colors"
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
