"use client";

import { useMemo, useRef, useState } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Phone, PhoneOff, PhoneIncoming, Clock, ChevronRight, ChevronDown, FileText, Megaphone, Loader2, Sparkles, Play, Pause } from "lucide-react";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import { useCalls, useCallTranscript, useCallSummary } from "@/lib/api-hooks";
import type { Call } from "@/lib/dashboard-api";
import { CallSummaryCard } from "@/components/calls/CallSummaryCard";
import { statusPillClass } from "@/lib/status-colors";
import { extendedApi } from "@/lib/extended-api";
import { CallIssuesBanner } from "@/components/calls/call-issues-banner";

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

// Delegates to the shared util so call history, detail, and contacts all agree
// on what green/red mean.
const getStatusStyle = statusPillClass;

function formatDuration(seconds?: number) {
    if (!seconds) return "--";
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${secs.toString().padStart(2, "0")}`;
}

function humanizeOutcome(outcome?: string) {
    if (!outcome) return "--";
    if (outcome === "goal_achieved") return "Qualified";
    if (outcome === "goal_not_achieved") return "Disqualified";
    return outcome.replace(/_/g, " ");
}

function CallRow({ call }: { call: Call }) {
    const [expanded, setExpanded] = useState(false);
    const [showTranscript, setShowTranscript] = useState(false);
    const summaryQuery = useCallSummary(expanded ? call.id : undefined);
    const transcriptQuery = useCallTranscript(showTranscript ? call.id : undefined, "json");

    // Inline recording playback (same auth/refresh path as the detail page).
    const audioRef = useRef<HTMLAudioElement | null>(null);
    const [audioUrl, setAudioUrl] = useState<string | null>(null);
    const [audioLoading, setAudioLoading] = useState(false);
    const [playing, setPlaying] = useState(false);

    const togglePlay = async () => {
        const el = audioRef.current;
        if (!el || !call.recording_id) return;
        if (playing) { el.pause(); return; }
        try {
            if (!audioUrl) {
                setAudioLoading(true);
                const url = await extendedApi.fetchRecordingBlob(call.recording_id);
                setAudioUrl(url);
                el.src = url;
            }
            await el.play();
        } catch {
            // Playback failed (e.g. recording gone) — leave the button idle.
        } finally {
            setAudioLoading(false);
        }
    };

    return (
        <div className="rounded-xl border border-border bg-background">
            <div className="grid min-w-0 grid-cols-[minmax(0,1.4fr)_minmax(0,0.8fr)_minmax(0,1fr)_minmax(0,0.7fr)_minmax(0,1.1fr)_auto_auto_auto_auto] items-center gap-3 px-4 py-3">
                <div className="flex min-w-0 flex-col gap-0.5">
                    <div className="flex min-w-0 items-center gap-3">
                        {getStatusIcon(call.status)}
                        <span className="truncate text-sm font-semibold text-foreground">{call.phone_number}</span>
                    </div>
                    {call.summary && (
                        <p className="truncate pl-7 text-xs text-muted-foreground" title={call.summary}>
                            {call.summary}
                        </p>
                    )}
                    {call.lead_outcome && (
                        <span
                            className={`ml-7 w-fit rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide ${statusPillClass(call.lead_outcome.split("|")[0])}`}
                            title={call.lead_outcome}
                        >
                            {call.lead_outcome.split("|")[0].trim()}
                        </span>
                    )}
                </div>
                <div className="min-w-0">
                    <span className={`inline-flex items-center rounded-full px-2 py-1 text-xs font-semibold ${getStatusStyle(call.status)}`}>
                        {call.status}
                    </span>
                </div>
                <div className="min-w-0">
                    {call.outcome ? (
                        <span className={`inline-flex items-center rounded-full px-2 py-1 text-xs font-semibold ${statusPillClass(call.outcome)}`}>
                            {humanizeOutcome(call.outcome)}
                        </span>
                    ) : (
                        <span className="text-sm text-muted-foreground">--</span>
                    )}
                </div>
                <div className="flex items-center gap-1 text-sm text-muted-foreground tabular-nums">
                    <Clock className="h-4 w-4" />
                    {formatDuration(call.duration_seconds)}
                </div>
                <div className="min-w-0 truncate text-sm text-muted-foreground">{new Date(call.created_at).toLocaleString()}</div>
                <button
                    type="button"
                    onClick={() => setExpanded((v) => !v)}
                    aria-expanded={expanded}
                    aria-label={expanded ? "Hide AI summary" : "Show AI summary"}
                    title={expanded ? "Hide AI summary" : "Show AI summary"}
                    className={`inline-flex h-8 w-8 items-center justify-center rounded-lg border transition-colors ${expanded
                        ? "border-ring/60 bg-accent text-accent-foreground"
                        : "border-border bg-background text-muted-foreground hover:text-foreground hover:bg-accent"
                        }`}
                >
                    <Sparkles className="h-4 w-4" />
                </button>
                <button
                    type="button"
                    onClick={() => setShowTranscript((v) => !v)}
                    aria-expanded={showTranscript}
                    aria-label={showTranscript ? "Hide transcript" : "Show transcript"}
                    title={showTranscript ? "Hide transcript" : "Show transcript"}
                    className={`inline-flex h-8 w-8 items-center justify-center rounded-lg border transition-colors ${showTranscript
                        ? "border-ring/60 bg-accent text-accent-foreground"
                        : "border-border bg-background text-muted-foreground hover:text-foreground hover:bg-accent"
                        }`}
                >
                    <FileText className="h-4 w-4" />
                </button>
                {call.recording_id ? (
                    <button
                        type="button"
                        onClick={togglePlay}
                        aria-label={playing ? "Pause recording" : "Play recording"}
                        title={playing ? "Pause recording" : "Play recording"}
                        className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-border bg-background text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                    >
                        {audioLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
                    </button>
                ) : (
                    <span aria-hidden className="inline-block h-8 w-8" />
                )}
                <Link
                    href={`/calls/${call.id}`}
                    aria-label="Open call details"
                    className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-border bg-background text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                >
                    <ChevronRight className="h-4 w-4" />
                </Link>
            </div>
            <audio
                ref={audioRef}
                hidden
                onPlay={() => setPlaying(true)}
                onPause={() => setPlaying(false)}
                onEnded={() => setPlaying(false)}
            />

            <AnimatePresence initial={false}>
                {expanded && (
                    <motion.div
                        key="summary"
                        initial={{ opacity: 0, height: 0 }}
                        animate={{ opacity: 1, height: "auto" }}
                        exit={{ opacity: 0, height: 0 }}
                        transition={{ duration: 0.18 }}
                        className="overflow-hidden border-t border-border bg-muted/40"
                    >
                        <div className="px-4 py-3">
                            <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                                <Sparkles className="h-3.5 w-3.5" />
                                AI Summary
                            </div>
                            <CallSummaryCard
                                isLoading={summaryQuery.isLoading}
                                isError={summaryQuery.isError}
                                error={summaryQuery.error}
                                data={summaryQuery.data}
                            />
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>

            <AnimatePresence initial={false}>
                {showTranscript && (
                    <motion.div
                        key="transcript"
                        initial={{ opacity: 0, height: 0 }}
                        animate={{ opacity: 1, height: "auto" }}
                        exit={{ opacity: 0, height: 0 }}
                        transition={{ duration: 0.18 }}
                        className="overflow-hidden border-t border-border bg-muted/40"
                    >
                        <div className="px-4 py-3">
                            <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                                <FileText className="h-3.5 w-3.5" />
                                Transcript
                            </div>
                            {transcriptQuery.isLoading ? (
                                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                                    <Loader2 className="h-4 w-4 animate-spin" />
                                    Loading transcript…
                                </div>
                            ) : transcriptQuery.isError ? (
                                <p className="text-sm text-destructive">
                                    {transcriptQuery.error instanceof Error ? transcriptQuery.error.message : "Failed to load transcript."}
                                </p>
                            ) : transcriptQuery.data?.turns && transcriptQuery.data.turns.length > 0 ? (
                                <div className="max-h-72 space-y-2 overflow-y-auto pr-1">
                                    {transcriptQuery.data.turns.map((turn, i) => {
                                        const isAgent = turn.role === "assistant" || turn.role === "agent";
                                        return (
                                            <div key={i} className="flex gap-2">
                                                <span className={`shrink-0 rounded-md border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide ${isAgent
                                                    ? "border-cyan-500/40 bg-cyan-500/10 text-cyan-700 dark:text-cyan-300"
                                                    : "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300"
                                                    }`}>
                                                    {isAgent ? "Agent" : "Caller"}
                                                </span>
                                                <p className="text-sm text-foreground leading-relaxed">{turn.content}</p>
                                            </div>
                                        );
                                    })}
                                </div>
                            ) : transcriptQuery.data?.transcript ? (
                                <pre className="max-h-72 overflow-auto whitespace-pre-wrap text-sm text-foreground">{transcriptQuery.data.transcript}</pre>
                            ) : (
                                <p className="text-sm text-muted-foreground">No transcript available for this call.</p>
                            )}
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
}

type CampaignGroup = {
    id: string;
    name: string;
    calls: Call[];
    completed: number;
    failed: number;
    totalDuration: number;
    latest: number; // ms
};

function groupByCampaign(calls: Call[]): CampaignGroup[] {
    const map = new Map<string, CampaignGroup>();
    for (const c of calls) {
        const id = c.campaign_id || "__none__";
        const name = c.campaign_name || "No campaign";
        let g = map.get(id);
        if (!g) {
            g = { id, name, calls: [], completed: 0, failed: 0, totalDuration: 0, latest: 0 };
            map.set(id, g);
        }
        g.calls.push(c);
        if (c.status === "completed" || c.status === "answered") g.completed++;
        if (c.status === "failed" || c.status === "no_answer" || c.status === "busy") g.failed++;
        if (c.duration_seconds) g.totalDuration += c.duration_seconds;
        const ts = new Date(c.created_at).getTime();
        if (Number.isFinite(ts) && ts > g.latest) g.latest = ts;
    }
    for (const g of map.values()) {
        g.calls.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
    }
    return Array.from(map.values()).sort((a, b) => b.latest - a.latest);
}

function CampaignSection({ group, defaultOpen }: { group: CampaignGroup; defaultOpen: boolean }) {
    const [open, setOpen] = useState(defaultOpen);

    return (
        <motion.section
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            className="content-card overflow-hidden"
        >
            <button
                type="button"
                onClick={() => setOpen((v) => !v)}
                aria-expanded={open}
                className="flex w-full items-center justify-between gap-3 text-left"
            >
                <div className="flex min-w-0 items-center gap-3">
                    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-border bg-muted/60">
                        <Megaphone className="h-4 w-4 text-muted-foreground" />
                    </div>
                    <div className="min-w-0">
                        <h3 className="truncate text-sm font-semibold text-foreground">{group.name}</h3>
                        <p className="text-xs text-muted-foreground tabular-nums">
                            {group.calls.length} call{group.calls.length === 1 ? "" : "s"} · {formatDuration(group.totalDuration)} total
                        </p>
                    </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                    <span className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-xs font-semibold text-emerald-700 dark:text-emerald-300 tabular-nums">
                        {group.completed} answered
                    </span>
                    <span className="rounded-full border border-red-500/30 bg-red-500/10 px-2 py-0.5 text-xs font-semibold text-red-700 dark:text-red-300 tabular-nums">
                        {group.failed} failed
                    </span>
                    <ChevronDown className={`h-4 w-4 text-muted-foreground transition-transform ${open ? "rotate-0" : "-rotate-90"}`} />
                </div>
            </button>

            <AnimatePresence initial={false}>
                {open && (
                    <motion.div
                        key="rows"
                        initial={{ opacity: 0, height: 0 }}
                        animate={{ opacity: 1, height: "auto" }}
                        exit={{ opacity: 0, height: 0 }}
                        transition={{ duration: 0.2 }}
                        className="overflow-hidden"
                    >
                        <div className="mt-4 hidden grid-cols-[minmax(0,1.4fr)_minmax(0,0.8fr)_minmax(0,1fr)_minmax(0,0.7fr)_minmax(0,1.1fr)_auto_auto_auto] gap-3 px-4 pb-2 text-left text-xs font-semibold uppercase tracking-wider text-muted-foreground md:grid">
                            <div>Phone</div>
                            <div>Status</div>
                            <div>Outcome</div>
                            <div>Duration</div>
                            <div>Date</div>
                            <div className="text-center">AI</div>
                            <div className="text-center">Script</div>
                            <div />
                        </div>
                        <div className="space-y-2">
                            {group.calls.map((call) => (
                                <CallRow key={call.id} call={call} />
                            ))}
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>
        </motion.section>
    );
}

export default function CallsPage() {
    const [page, setPage] = useState(1);
    const pageSize = 50;

    const q = useCalls(page, pageSize);
    const calls = useMemo(() => q.data?.calls ?? [], [q.data]);
    const total = q.data?.total ?? 0;
    const error = q.isError ? (q.error instanceof Error ? q.error.message : "Failed to load calls") : "";
    const groups = useMemo(() => groupByCampaign(calls), [calls]);
    const totalPages = Math.ceil(total / pageSize);

    return (
        <DashboardLayout title="Call History" description="Calls grouped by campaign — tap the script icon to view a transcript">
            <CallIssuesBanner />
            {q.isLoading ? (
                <div className="flex items-center justify-center h-64">
                    <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-foreground/60" />
                </div>
            ) : error ? (
                <div className="content-card border-destructive/30 text-destructive">{error}</div>
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
                    <p className="text-muted-foreground">Start a campaign to begin making calls.</p>
                </motion.div>
            ) : (
                <div className="space-y-4">
                    {groups.map((g, idx) => (
                        <CampaignSection key={g.id} group={g} defaultOpen={idx === 0} />
                    ))}

                    {totalPages > 1 && (
                        <motion.div
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            transition={{ delay: 0.3 }}
                            className="flex items-center justify-between"
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
                </div>
            )}
        </DashboardLayout>
    );
}
