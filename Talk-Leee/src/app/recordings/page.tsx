"use client";

import { useCallback, useEffect, useState } from "react";
import { Clock } from "lucide-react";
import { motion } from "framer-motion";

import { RecordingFeedbackBar } from "@/components/calls/recording-feedback-bar";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { RecordingMediaControls } from "@/components/recordings/recording-media-controls";
import { EmptyState, ErrorState, LoadingState, failedReadMessage } from "@/components/states/page-states";
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
    // False until the first page has answered: only that first load replaces
    // the list. On a page change the current list stays while the next loads.
    const [loaded, setLoaded] = useState(false);
    const [reload, setReload] = useState(0);
    const [error, setError] = useState("");
    const [page, setPage] = useState(1);
    const [total, setTotal] = useState(0);

    useEffect(() => {
        if (!canRead) return;

        const controller = new AbortController();
        void (async () => {
            try {
                setError("");
                const response = await extendedApi.listRecordings(
                    undefined,
                    page,
                    PAGE_SIZE,
                    controller.signal,
                );
                if (controller.signal.aborted) return;
                // An answer without its list is a failed read, not "no recordings".
                if (!Array.isArray(response?.items)) throw new Error();
                setRecordings(response.items);
                setTotal(response.total);
                setLoaded(true);
            } catch (err) {
                if (controller.signal.aborted) return;
                setError(failedReadMessage(err, "Failed to load recordings"));
            }
        })();

        return () => controller.abort();
    }, [canRead, page, reload]);

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
            {checkingPermissions || (canRead && !loaded && !error) ? (
                <LoadingState />
            ) : permissions.isError ? (
                <ErrorState message="Recording permissions could not be verified. Please refresh and try again." onRetry={() => void permissions.refetch()} />
            ) : !canRead ? (
                <ErrorState message="You do not have permission to view or play call recordings." onRetry={() => void permissions.refetch()} />
            ) : error ? (
                <ErrorState message={error} onRetry={() => setReload((current) => current + 1)} />
            ) : recordings.length === 0 ? (
                <EmptyState title="No recordings yet" message="Recordings will appear here after calls are completed." />
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
                            className="mt-6 flex items-center justify-between pr-12"
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
