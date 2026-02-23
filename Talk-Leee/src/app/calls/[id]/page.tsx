"use client";

import { useMemo } from "react";
import { useParams, useRouter } from "next/navigation";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { ArrowLeft, Phone, Clock, FileText, Play } from "lucide-react";
import { motion } from "framer-motion";
import { useCall, useCallTranscript } from "@/lib/api-hooks";

function getStatusStyle(status: string) {
    switch (status) {
        case "answered":
        case "completed":
            return "bg-background text-emerald-800 border border-emerald-700/40 dark:text-emerald-300 dark:border-emerald-400/50";
        case "failed":
        case "no_answer":
        case "busy":
            return "bg-background text-red-800 border border-red-700/40 dark:text-red-300 dark:border-red-400/50";
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

function formatTurnTimestamp(ts: string) {
    const parsed = Date.parse(ts);
    if (Number.isFinite(parsed)) return new Date(ts).toLocaleTimeString();
    return ts;
}

interface TranscriptTurn {
    role: string;
    content: string;
    timestamp: string;
}

export default function CallDetailPage() {
    const params = useParams();
    const router = useRouter();
    const callId = params.id as string;

    const callQuery = useCall(callId);
    const transcriptQuery = useCallTranscript(callId, "json");
    const call = callQuery.data ?? null;
    const transcript = useMemo(() => (transcriptQuery.data?.turns ?? []) as TranscriptTurn[], [transcriptQuery.data?.turns]);
    const error = callQuery.isError ? (callQuery.error instanceof Error ? callQuery.error.message : "Failed to load call details") : "";

    return (
        <DashboardLayout title="Call Details" description="Transcript, recording, and metadata for this call.">
            <motion.div
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                className="mb-6"
            >
                <Button variant="ghost" size="sm" onClick={() => router.back()} className="gap-2 px-2">
                    <ArrowLeft className="h-4 w-4" />
                    Back to calls
                </Button>
            </motion.div>

            {callQuery.isLoading ? (
                <div className="flex items-center justify-center h-64">
                    <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-foreground/60" />
                </div>
            ) : error ? (
                <div className="content-card border-destructive/30 text-destructive">
                    {error}
                </div>
            ) : call ? (
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                    {/* Call Info */}
                    <div className="lg:col-span-1 space-y-6">
                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            className="content-card"
                        >
                            <div className="flex items-center justify-between gap-3 mb-4">
                                <h2 className="text-sm font-semibold text-foreground">Call Details</h2>
                                <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold ${getStatusStyle(call.status)}`}>
                                    {call.status}
                                </span>
                            </div>

                            <div className="space-y-3">
                                <div className="group flex items-center gap-3 rounded-2xl border border-border bg-muted/60 p-3 shadow-sm transition-[transform,background-color,border-color,box-shadow] duration-150 ease-out hover:-translate-y-0.5 hover:bg-background hover:shadow-md">
                                    <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-background/60 text-foreground transition-colors group-hover:bg-background">
                                        <Phone className="h-5 w-5" />
                                    </div>
                                    <div className="min-w-0 flex-1">
                                        <div className="text-[11px] font-bold uppercase tracking-wide text-muted-foreground">Phone Number</div>
                                        <div className="mt-0.5 truncate text-sm font-semibold text-foreground">{call.phone_number}</div>
                                    </div>
                                </div>

                                <div className="group flex items-center gap-3 rounded-2xl border border-border bg-muted/60 p-3 shadow-sm transition-[transform,background-color,border-color,box-shadow] duration-150 ease-out hover:-translate-y-0.5 hover:bg-background hover:shadow-md">
                                    <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-background/60 text-foreground transition-colors group-hover:bg-background">
                                        <Clock className="h-5 w-5" />
                                    </div>
                                    <div className="min-w-0 flex-1">
                                        <div className="text-[11px] font-bold uppercase tracking-wide text-muted-foreground">Duration</div>
                                        <div className="mt-0.5 text-sm font-semibold text-foreground tabular-nums">
                                            {formatDuration(call.duration_seconds)}
                                        </div>
                                    </div>
                                </div>

                                {call.outcome ? (
                                    <div className="group rounded-2xl border border-border bg-muted/60 p-3 shadow-sm transition-[transform,background-color,border-color,box-shadow] duration-150 ease-out hover:-translate-y-0.5 hover:bg-background hover:shadow-md">
                                        <div className="text-[11px] font-bold uppercase tracking-wide text-muted-foreground">Outcome</div>
                                        <div className="mt-1 text-sm font-semibold text-foreground">{call.outcome}</div>
                                    </div>
                                ) : null}

                                <div className="group rounded-2xl border border-border bg-muted/60 p-3 shadow-sm transition-[transform,background-color,border-color,box-shadow] duration-150 ease-out hover:-translate-y-0.5 hover:bg-background hover:shadow-md">
                                    <div className="text-[11px] font-bold uppercase tracking-wide text-muted-foreground">Date</div>
                                    <div className="mt-1 text-sm font-semibold text-foreground">{new Date(call.created_at).toLocaleString()}</div>
                                </div>
                            </div>
                        </motion.div>

                        {call.summary && (
                            <motion.div
                                initial={{ opacity: 0, y: 20 }}
                                animate={{ opacity: 1, y: 0 }}
                                transition={{ delay: 0.1 }}
                                whileHover={{ scale: 1.01 }}
                                className="content-card"
                            >
                                <h2 className="text-sm font-semibold text-foreground mb-4">Summary</h2>
                                <div className="rounded-2xl border border-border bg-muted/60 p-4 shadow-sm transition-[transform,background-color,box-shadow] duration-150 ease-out hover:-translate-y-0.5 hover:bg-background hover:shadow-md">
                                    <p className="text-sm leading-relaxed text-muted-foreground">{call.summary}</p>
                                </div>
                            </motion.div>
                        )}

                        {call.recording_id && (
                            <motion.div
                                initial={{ opacity: 0, y: 20 }}
                                animate={{ opacity: 1, y: 0 }}
                                transition={{ delay: 0.2 }}
                                whileHover={{ scale: 1.01 }}
                                className="content-card"
                            >
                                <h2 className="text-sm font-semibold text-foreground mb-4">Recording</h2>
                                <div className="rounded-2xl border border-border bg-muted/60 p-4 shadow-sm transition-[transform,background-color,box-shadow] duration-150 ease-out hover:-translate-y-0.5 hover:bg-background hover:shadow-md">
                                    <Button variant="outline" className="w-full hover:scale-[1.02] hover:shadow-md active:scale-[0.99]">
                                        <Play className="w-4 h-4" />
                                        Play Recording
                                    </Button>
                                </div>
                            </motion.div>
                        )}
                    </div>

                    {/* Transcript */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.3 }}
                        className="lg:col-span-2"
                    >
                        <div className="content-card">
                            <h2 className="mb-4 flex items-center gap-2 text-sm font-semibold text-foreground">
                                <FileText className="h-5 w-5 text-muted-foreground" aria-hidden />
                                Transcript
                            </h2>
                            {transcript.length === 0 ? (
                                <div className="py-8 text-center text-sm text-muted-foreground">
                                    No transcript available
                                </div>
                            ) : (
                                <div className="rounded-2xl border border-border bg-muted/60 p-4 shadow-sm">
                                    <div className="space-y-3">
                                    {transcript.map((turn, index) => (
                                        <motion.div
                                            key={index}
                                            initial={{ opacity: 0, x: turn.role === "assistant" ? -10 : 10 }}
                                            animate={{ opacity: 1, x: 0 }}
                                            transition={{ delay: 0.4 + index * 0.05 }}
                                            whileHover={{ scale: 1.01 }}
                                            className={`flex gap-3 ${turn.role === "assistant" ? "flex-row" : "flex-row-reverse"}`}
                                        >
                                            <div
                                                className={`flex h-9 w-9 items-center justify-center rounded-full border border-border text-xs font-bold ${turn.role === "assistant" ? "bg-muted/60 text-foreground" : "bg-muted/80 text-foreground"}`}
                                            >
                                                {turn.role === "assistant" ? "AI" : "U"}
                                            </div>
                                            <div
                                                className={`flex-1 max-w-[82%] rounded-2xl border p-4 shadow-sm transition-[transform,box-shadow] duration-150 ease-out hover:-translate-y-0.5 hover:shadow-md ${turn.role === "assistant" ? "border-border bg-background" : "border-border bg-muted/60"}`}
                                            >
                                                <p className="text-sm text-foreground">{turn.content}</p>
                                                <p className="mt-2 text-xs text-muted-foreground">
                                                    {formatTurnTimestamp(turn.timestamp)}
                                                </p>
                                            </div>
                                        </motion.div>
                                    ))}
                                    </div>
                                </div>
                            )}
                        </div>
                    </motion.div>
                </div>
            ) : null}
        </DashboardLayout>
    );
}
