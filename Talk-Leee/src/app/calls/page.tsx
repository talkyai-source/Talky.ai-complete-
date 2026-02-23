"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Phone, PhoneOff, PhoneIncoming, Clock, ChevronRight } from "lucide-react";
import Link from "next/link";
import { motion } from "framer-motion";
import { useCalls } from "@/lib/api-hooks";

function getStatusIcon(status: string) {
    switch (status) {
        case "answered":
        case "completed":
            return <Phone className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />;
        case "failed":
        case "no_answer":
        case "busy":
            return <PhoneOff className="h-4 w-4 text-red-600 dark:text-red-400" />;
        case "in_progress":
            return <PhoneIncoming className="h-4 w-4 text-blue-600 dark:text-blue-400" />;
        default:
            return <Phone className="h-4 w-4 text-muted-foreground" />;
    }
}

function getStatusStyle(status: string) {
    switch (status) {
        case "answered":
        case "completed":
            return "bg-background text-emerald-800 border border-emerald-700/50 dark:text-emerald-300 dark:border-emerald-400/50";
        case "failed":
        case "no_answer":
        case "busy":
            return "bg-background text-red-800 border border-red-700/50 dark:text-red-300 dark:border-red-400/50";
        case "in_progress":
            return "bg-background text-blue-800 border border-blue-700/50 dark:text-blue-300 dark:border-blue-400/50";
        default:
            return "bg-background text-muted-foreground border border-border";
    }
}

function formatDuration(seconds?: number) {
    if (!seconds) return "--";
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${secs.toString().padStart(2, "0")}`;
}

export default function CallsPage() {
    const [page, setPage] = useState(1);
    const pageSize = 20;
    const ROWS_VISIBLE = 7;
    const firstRowRef = useRef<HTMLAnchorElement | null>(null);
    const [rowsMaxHeightPx, setRowsMaxHeightPx] = useState<number | null>(null);

    const q = useCalls(page, pageSize);
    const calls = useMemo(() => q.data?.calls ?? [], [q.data]);
    const total = q.data?.total ?? 0;
    const error = q.isError ? (q.error instanceof Error ? q.error.message : "Failed to load calls") : "";

    const totalPages = Math.ceil(total / pageSize);

    useEffect(() => {
        const gapPx = 8;

        const measure = () => {
            const el = firstRowRef.current;
            if (!el) {
                setRowsMaxHeightPx(null);
                return;
            }
            const h = el.getBoundingClientRect().height;
            if (!Number.isFinite(h) || h <= 0) return;
            const visible = Math.max(1, ROWS_VISIBLE);
            const maxHeight = Math.round(h * visible + gapPx * (visible - 1));
            setRowsMaxHeightPx(maxHeight);
        };

        const raf = window.requestAnimationFrame(measure);
        window.addEventListener("resize", measure, { passive: true });
        return () => {
            window.cancelAnimationFrame(raf);
            window.removeEventListener("resize", measure);
        };
    }, [ROWS_VISIBLE, calls.length]);

    return (
        <DashboardLayout title="Call History" description="View all calls and their transcripts">
            {q.isLoading ? (
                <div className="flex items-center justify-center h-64">
                    <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-foreground/60" />
                </div>
            ) : error ? (
                <div className="content-card border-destructive/30 text-destructive">
                    {error}
                </div>
            ) : calls.length === 0 ? (
                <motion.div
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="content-card py-16 text-center"
                >
                    <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-muted/40">
                        <Phone className="h-8 w-8 text-muted-foreground" />
                    </div>
                    <h3 className="mb-2 text-lg font-semibold text-foreground">No calls yet</h3>
                    <p className="text-muted-foreground">
                        Start a campaign to begin making calls.
                    </p>
                </motion.div>
            ) : (
                <>
                    {/* Calls Table */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        className="content-card overflow-hidden"
                    >
                        <div className="flex items-center justify-between gap-3">
                            <div className="text-sm font-semibold text-foreground">Calls</div>
                            <div className="inline-flex items-center rounded-lg border border-border bg-background px-2 py-0.5 text-[11px] font-semibold text-muted-foreground tabular-nums">
                                {total}
                            </div>
                        </div>

                        <div className="mt-4 min-w-0 overflow-x-hidden">
                            <div className="grid min-w-0 grid-cols-[minmax(0,1.4fr)_minmax(0,0.8fr)_minmax(0,1fr)_minmax(0,0.7fr)_minmax(0,1.2fr)_auto] gap-3 border-b border-border px-4 pb-3 text-left text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                                <div>Phone Number</div>
                                <div>Status</div>
                                <div>Outcome</div>
                                <div>Duration</div>
                                <div>Date</div>
                                <div />
                            </div>

                            <div
                                className="mt-3 space-y-2 overflow-x-hidden overflow-y-auto overscroll-contain px-1 pr-1 scrollbar-gutter-stable"
                                style={rowsMaxHeightPx ? { maxHeight: rowsMaxHeightPx } : undefined}
                            >
                                {calls.map((call, index) => (
                                    <motion.div
                                        key={call.id}
                                        initial={{ opacity: 0, x: -10 }}
                                        animate={{ opacity: 1, x: 0 }}
                                        transition={{ delay: index * 0.02 }}
                                    >
                                        <Link
                                            href={`/calls/${call.id}`}
                                            ref={index === 0 ? firstRowRef : undefined}
                                            className="group grid min-w-0 grid-cols-[minmax(0,1.4fr)_minmax(0,0.8fr)_minmax(0,1fr)_minmax(0,0.7fr)_minmax(0,1.2fr)_auto] items-center gap-3 rounded-xl border border-border bg-background px-4 py-3 text-left transition-transform duration-150 ease-out hover:scale-[1.01] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                                        >
                                            <div className="flex min-w-0 items-center gap-3">
                                                {getStatusIcon(call.status)}
                                                <span className="truncate text-sm font-semibold text-foreground">{call.phone_number}</span>
                                            </div>
                                            <div className="min-w-0">
                                                <span className={`inline-flex items-center rounded-full px-2 py-1 text-xs font-semibold ${getStatusStyle(call.status)}`}>
                                                    {call.status}
                                                </span>
                                            </div>
                                            <div className="min-w-0 truncate text-sm text-muted-foreground">{call.outcome || "--"}</div>
                                            <div className="flex items-center gap-1 text-sm text-muted-foreground tabular-nums">
                                                <Clock className="h-4 w-4" />
                                                {formatDuration(call.duration_seconds)}
                                            </div>
                                            <div className="min-w-0 truncate text-sm text-muted-foreground">{new Date(call.created_at).toLocaleString()}</div>
                                            <div className="flex items-center justify-end text-muted-foreground group-hover:text-foreground">
                                                <ChevronRight className="h-5 w-5" />
                                            </div>
                                        </Link>
                                    </motion.div>
                                ))}
                            </div>
                        </div>
                    </motion.div>

                    {/* Pagination */}
                    {totalPages > 1 && (
                        <motion.div
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            transition={{ delay: 0.3 }}
                            className="flex items-center justify-between mt-6"
                        >
                            <p className="text-sm text-muted-foreground">
                                Showing {(page - 1) * pageSize + 1} to {Math.min(page * pageSize, total)} of {total} calls
                            </p>
                            <div className="flex gap-2">
                                <button
                                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                                    disabled={page === 1}
                                    className="rounded-lg border border-border bg-background px-3 py-1.5 text-sm font-semibold text-foreground transition-colors duration-150 ease-out hover:bg-accent hover:text-accent-foreground disabled:cursor-not-allowed disabled:opacity-50"
                                >
                                    Previous
                                </button>
                                <button
                                    onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                                    disabled={page === totalPages}
                                    className="rounded-lg border border-border bg-background px-3 py-1.5 text-sm font-semibold text-foreground transition-colors duration-150 ease-out hover:bg-accent hover:text-accent-foreground disabled:cursor-not-allowed disabled:opacity-50"
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
