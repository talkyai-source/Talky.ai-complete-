"use client";

import { useCallback, useEffect, useState } from "react";
import { Clock, Volume2 } from "lucide-react";
import { motion } from "framer-motion";

import { RecordingFeedbackBar } from "@/components/calls/recording-feedback-bar";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { RecordingMediaControls } from "@/components/recordings/recording-media-controls";
import { extendedApi, type Recording } from "@/lib/extended-api";
import { getRecordingCapabilities } from "@/lib/media-permissions";
import { useEffectivePermissions } from "@/lib/queries/inbound-queries";

const PAGE_SIZE = 20;

function formatDuration(seconds?: number | null) {
    if (!seconds) return "--:--";
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${secs.toString().padStart(2, "0")}`;
}

export default function RecordingsPage() {
    const permissions = useEffectivePermissions();
    const capabilities = getRecordingCapabilities(
        permissions.isSuccess ? permissions.data.permissions : undefined,
    );
    const canRead = permissions.isSuccess && capabilities.canRead;

    const [recordings, setRecordings] = useState<Recording[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const [page, setPage] = useState(1);
    const [total, setTotal] = useState(0);

    useEffect(() => {
        if (!canRead) return;

        const controller = new AbortController();
        void (async () => {
            try {
                setLoading(true);
                setError("");
                const response = await extendedApi.listRecordings(
                    undefined,
                    page,
                    PAGE_SIZE,
                    controller.signal,
                );
                if (controller.signal.aborted) return;
                setRecordings(response.items);
                setTotal(response.total);
            } catch (err) {
                if (controller.signal.aborted) return;
                setError(err instanceof Error ? err.message : "Failed to load recordings");
            } finally {
                if (!controller.signal.aborted) setLoading(false);
            }
        })();

        return () => controller.abort();
    }, [canRead, page]);

    const handleDeleted = useCallback((recordingId: string) => {
        const wasOnlyRow = recordings.length === 1;
        setRecordings((current) => current.filter((recording) => recording.id !== recordingId));
        setTotal((current) => Math.max(0, current - 1));
        if (wasOnlyRow && page > 1) setPage((current) => Math.max(1, current - 1));
    }, [page, recordings.length]);

    const totalPages = Math.ceil(total / PAGE_SIZE);
    const checkingPermissions = permissions.isLoading || permissions.isPending;

    return (
        <DashboardLayout title="Recordings" description="Listen to and manage call recordings">
            {checkingPermissions || (canRead && loading) ? (
                <div className="flex h-64 items-center justify-center">
                    <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-foreground/60" />
                </div>
            ) : permissions.isError ? (
                <div className="content-card border-amber-500/30 text-amber-700 dark:text-amber-300" role="alert">
                    Recording permissions could not be verified. Please refresh and try again.
                </div>
            ) : !canRead ? (
                <div className="content-card text-muted-foreground">
                    You do not have permission to view or play call recordings.
                </div>
            ) : error ? (
                <div className="content-card border-red-500/30 text-red-600 dark:text-red-400" role="alert">
                    {error}
                </div>
            ) : recordings.length === 0 ? (
                <motion.div
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="content-card py-16 text-center"
                >
                    <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-foreground/5">
                        <Volume2 className="h-8 w-8 text-muted-foreground" />
                    </div>
                    <h2 className="mb-2 text-lg font-medium text-foreground">No recordings yet</h2>
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
                                className="rounded-2xl border border-border bg-muted/60 p-6 shadow-sm transition-[background-color,box-shadow] duration-150 ease-out hover:bg-background hover:shadow-md"
                            >
                                <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                                    <div className="flex items-center gap-4">
                                        <span className="text-sm font-semibold text-foreground">
                                            {recording.phone_number || `Call ${recording.call_id.slice(0, 8)}…`}
                                        </span>
                                        <span className="flex items-center gap-1 text-sm text-foreground/70">
                                            <Clock className="h-4 w-4" />
                                            {formatDuration(recording.duration_seconds)}
                                        </span>
                                    </div>
                                    <span className="text-sm text-foreground/70">
                                        {new Date(recording.created_at).toLocaleString()}
                                    </span>
                                </div>

                                <RecordingMediaControls
                                    recording={recording}
                                    canPlay={capabilities.canRead}
                                    canDownload={capabilities.canDownload}
                                    canDelete={capabilities.canDelete}
                                    onDeleted={handleDeleted}
                                />

                                <RecordingFeedbackBar callId={recording.call_id} className="mt-3" />
                            </motion.div>
                        ))}
                    </div>

                    {totalPages > 1 ? (
                        <motion.div
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            transition={{ delay: 0.3 }}
                            className="mt-6 flex items-center justify-between"
                        >
                            <p className="text-sm text-muted-foreground">
                                Showing {(page - 1) * PAGE_SIZE + 1} to {Math.min(page * PAGE_SIZE, total)} of {total} recordings
                            </p>
                            <div className="flex gap-2">
                                <button
                                    type="button"
                                    onClick={() => setPage((current) => Math.max(1, current - 1))}
                                    disabled={page === 1}
                                    className="rounded-md border border-border px-3 py-1 text-sm text-foreground transition hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
                                >
                                    Previous
                                </button>
                                <button
                                    type="button"
                                    onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
                                    disabled={page === totalPages}
                                    className="rounded-md border border-border px-3 py-1 text-sm text-foreground transition hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
                                >
                                    Next
                                </button>
                            </div>
                        </motion.div>
                    ) : null}
                </>
            )}
        </DashboardLayout>
    );
}
