"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Phone, PhoneOff, PhoneIncoming, PhoneOutgoing, Clock, ChevronRight, ChevronDown, FileText, Megaphone, Loader2, Sparkles, Play, Pause, Search, Mic } from "lucide-react";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import { useCalls, useCallTranscript, useCallSummary } from "@/lib/api-hooks";
import type { Call } from "@/lib/dashboard-api";
import { CallSummaryCard } from "@/components/calls/CallSummaryCard";
import { statusPillClass } from "@/lib/status-colors";
import { extendedApi } from "@/lib/extended-api";
import { CallIssuesBanner } from "@/components/calls/call-issues-banner";
import { QuickReviewButtons } from "@/components/calls/quick-review-buttons";
import { getRecordingCapabilities } from "@/lib/media-permissions";
import { useEffectivePermissions } from "@/lib/queries/inbound-queries";

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

const DESKTOP_CALL_GRID =
    "grid-cols-[minmax(0,1.4fr)_minmax(0,0.8fr)_minmax(0,1fr)_minmax(0,0.7fr)_minmax(0,1.1fr)_auto_auto_auto_auto_auto_auto]";

const FAILED_CALL_OUTCOMES = new Set([
    "busy",
    "failed",
    "no_answer",
    "rejected",
    "timeout",
    "unavailable",
]);

function classifyCall(call: Call) {
    const status = call.status.trim().toLowerCase();
    const outcome = call.outcome?.trim().toLowerCase() ?? "";
    const failed =
        ["busy", "failed", "no_answer"].includes(status) ||
        FAILED_CALL_OUTCOMES.has(outcome);

    return {
        answered: !failed && ["answered", "completed"].includes(status),
        failed,
    };
}

function formatDuration(seconds?: number) {
    if (!seconds) return "--";
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${secs.toString().padStart(2, "0")}`;
}

// Friendly, first-class labels for each call disposition so the history
// reads like a human's notes ("No Answer", "Voicemail") rather than raw
// enum values. Unknown values fall back to Title-Cased words.
const OUTCOME_LABELS: Record<string, string> = {
    goal_achieved: "Qualified",
    goal_not_achieved: "Disqualified",
    answered: "Answered",
    no_answer: "No Answer",
    busy: "Busy",
    voicemail: "Voicemail",
    rejected: "Rejected",
    failed: "Failed",
    timeout: "Timed Out",
    unavailable: "Unavailable",
    disconnected: "Disconnected",
};

function humanizeOutcome(outcome?: string) {
    if (!outcome) return "--";
    const key = outcome.trim().toLowerCase();
    if (OUTCOME_LABELS[key]) return OUTCOME_LABELS[key];
    return key
        .replace(/_/g, " ")
        .replace(/\b\w/g, (c) => c.toUpperCase());
}

function DirectionBadge({ direction }: { direction?: "inbound" | "outbound" }) {
    const inbound = direction === "inbound";
    return (
        <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide ${inbound ? "border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-300" : "border-border bg-muted/40 text-muted-foreground"}`}>
            {inbound ? <PhoneIncoming className="h-3 w-3" aria-hidden /> : <PhoneOutgoing className="h-3 w-3" aria-hidden />}
            {inbound ? "Inbound" : "Outbound"}
        </span>
    );
}

function CallParties({ call }: { call: Call }) {
    if (call.direction === "inbound") {
        return <><span className="truncate text-sm font-semibold text-foreground">{call.from_number || "Private caller"}</span><span className="truncate text-xs text-muted-foreground">to {call.to_number || "assigned DID"}</span></>;
    }
    return <span className="truncate text-sm font-semibold text-foreground">{call.phone_number}</span>;
}

function CallRow({ call, canPlayMedia }: { call: Call; canPlayMedia: boolean }) {
    const [expanded, setExpanded] = useState(false);
    const [showTranscript, setShowTranscript] = useState(false);
    const summaryQuery = useCallSummary(expanded ? call.id : undefined);
    const transcriptQuery = useCallTranscript(showTranscript ? call.id : undefined, "json");

    // Inline recording playback (same auth/refresh path as the detail page).
    const audioRef = useRef<HTMLAudioElement | null>(null);
    const audioUrlRef = useRef<string | null>(null);
    const [audioLoading, setAudioLoading] = useState(false);
    const [playing, setPlaying] = useState(false);
    const mountedRef = useRef(true);
    const canPlayMediaRef = useRef(canPlayMedia);

    useEffect(() => {
        canPlayMediaRef.current = canPlayMedia;
    }, [canPlayMedia]);

    useEffect(() => {
        const audio = audioRef.current;
        mountedRef.current = true;
        return () => {
            mountedRef.current = false;
            audio?.pause();
            if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current);
            audioUrlRef.current = null;
        };
    }, []);

    useEffect(() => {
        if (!canPlayMedia && audioUrlRef.current) {
            audioRef.current?.pause();
            if (audioRef.current) audioRef.current.removeAttribute("src");
            URL.revokeObjectURL(audioUrlRef.current);
            audioUrlRef.current = null;
        }
    }, [canPlayMedia]);

    const togglePlay = async () => {
        const el = audioRef.current;
        if (!el || !call.recording_id || !canPlayMedia) return;
        if (playing) { el.pause(); return; }
        try {
            if (!audioUrlRef.current) {
                setAudioLoading(true);
                const blob = await extendedApi.fetchRecordingPlaybackBlob(call.recording_id);
                const url = URL.createObjectURL(blob);
                if (!mountedRef.current || !canPlayMediaRef.current) {
                    URL.revokeObjectURL(url);
                    return;
                }
                audioUrlRef.current = url;
                el.src = url;
            }
            await el.play();
        } catch {
            // Playback failed (e.g. recording gone) — leave the button idle.
        } finally {
            if (mountedRef.current) setAudioLoading(false);
        }
    };

    return (
        <div className="rounded-xl border border-border bg-background">
            <div className="space-y-3 p-4 xl:hidden">
                <div className="flex items-start justify-between gap-3">
                    <div className="flex min-w-0 items-start gap-2">{getStatusIcon(call.status)}<div className="flex min-w-0 flex-col"><CallParties call={call} /></div></div>
                    <DirectionBadge direction={call.direction} />
                </div>
                <div className="flex flex-wrap items-center gap-2"><span className={`inline-flex items-center rounded-full px-2 py-1 text-xs font-semibold ${getStatusStyle(call.status)}`}>{humanizeOutcome(call.status)}</span>{call.outcome ? <span className={`inline-flex items-center rounded-full px-2 py-1 text-xs font-semibold ${statusPillClass(call.outcome)}`}>{humanizeOutcome(call.outcome)}</span> : null}<span className="inline-flex items-center gap-1 text-xs text-muted-foreground"><Clock className="h-3.5 w-3.5" aria-hidden />{formatDuration(call.duration_seconds)}</span><time dateTime={call.created_at} className="text-xs text-muted-foreground">{new Date(call.created_at).toLocaleString()}</time></div>
                {call.summary ? <p className="line-clamp-2 text-xs text-muted-foreground">{call.summary}</p> : null}
                <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
                    <button type="button" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded} className="inline-flex h-11 items-center gap-1.5 rounded-lg border border-border px-3 text-xs font-medium text-muted-foreground hover:bg-accent hover:text-foreground"><Sparkles className="h-4 w-4" aria-hidden />AI summary</button>
                    <button type="button" onClick={() => setShowTranscript((value) => !value)} aria-expanded={showTranscript} className="inline-flex h-11 items-center gap-1.5 rounded-lg border border-border px-3 text-xs font-medium text-muted-foreground hover:bg-accent hover:text-foreground"><FileText className="h-4 w-4" aria-hidden />Transcript</button>
                    {call.recording_id && canPlayMedia ? <button type="button" onClick={togglePlay} aria-label={playing ? "Pause recording" : "Play recording"} className="inline-flex h-11 items-center gap-1.5 rounded-lg border border-border px-3 text-xs font-medium text-muted-foreground hover:bg-accent hover:text-foreground">{audioLoading ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : playing ? <Pause className="h-4 w-4" aria-hidden /> : <Play className="h-4 w-4" aria-hidden />}Recording</button> : null}
                    <QuickReviewButtons callId={call.id} touchFriendly />
                    <Link href={`/calls/${call.id}`} aria-label={call.has_feedback ? "Agent feedback left — open call" : "Leave agent feedback"} className={`inline-flex h-11 w-11 items-center justify-center rounded-lg border ${call.has_feedback ? "border-primary/50 bg-primary/10 text-primary" : "border-border text-muted-foreground hover:bg-accent hover:text-foreground"}`}><Mic className="h-4 w-4" aria-hidden /></Link>
                    <span className="flex-1" /><Link href={`/calls/${call.id}`} className="inline-flex h-11 items-center gap-1.5 rounded-lg border border-border px-3 text-xs font-semibold text-foreground hover:bg-accent">Details<ChevronRight className="h-4 w-4" aria-hidden /></Link>
                </div>
            </div>
            {/* Six `auto` columns for six action cells: summary, transcript,
                play, rate, voice-note, open. The rate cell holds two buttons
                but is ONE cell, so the row does not grow a column per icon. */}
            <div className={`hidden min-w-0 ${DESKTOP_CALL_GRID} items-center gap-3 px-4 py-3 xl:grid`}>
                <div className="flex min-w-0 flex-col gap-0.5">
                    <div className="flex min-w-0 items-center gap-3">
                        {getStatusIcon(call.status)}
                        <div className="flex min-w-0 flex-col"><CallParties call={call} /></div>
                    </div>
                    <DirectionBadge direction={call.direction} />
                    {call.summary && (
                        <p className="truncate pl-7 text-xs text-muted-foreground" title={call.summary}>
                            {call.summary}
                        </p>
                    )}
                    {call.lead_outcome && (() => {
                        const verdict = call.lead_outcome.split("|")[0].trim().toLowerCase();
                        const isLead = verdict.startsWith("qualified") || verdict.startsWith("callback");
                        return (
                            <span
                                className={`ml-7 w-fit rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide ${statusPillClass(verdict)}`}
                                title={call.lead_outcome}
                            >
                                {isLead ? "Lead — follow up" : verdict}
                            </span>
                        );
                    })()}
                </div>
                <div className="min-w-0">
                    <span className={`inline-flex items-center rounded-full px-2 py-1 text-xs font-semibold ${getStatusStyle(call.status)}`}>
                        {humanizeOutcome(call.status)}
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
                {call.recording_id && canPlayMedia ? (
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
                {/* Rate it right here, next to the audio you just heard
                    (goals.md §3). Navigating to the detail page to leave a
                    rating meant, in practice, that nobody rated anything while
                    working down a list. The full panel on the call page is
                    still where tags and a comment go. */}
                <QuickReviewButtons callId={call.id} />
                {/* Agent-feedback voice note. Filled when this call already has
                    one. `has_feedback` is computed per row by an EXISTS in the
                    calls list query, so it reflects reality rather than being a
                    flag that is always false. */}
                <Link
                    href={`/calls/${call.id}`}
                    aria-label={call.has_feedback ? "Agent feedback left — open call" : "Leave agent feedback"}
                    title={call.has_feedback ? "Agent feedback left" : "Leave agent feedback"}
                    className={`inline-flex h-8 w-8 items-center justify-center rounded-lg border transition-colors ${call.has_feedback
                        ? "border-primary/50 bg-primary/10 text-primary"
                        : "border-border bg-background text-muted-foreground hover:bg-accent hover:text-foreground"
                        }`}
                >
                    <Mic className="h-4 w-4" />
                </Link>
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
        const id = c.campaign_id || c.inbound_campaign_id || "__none__";
        const name = c.campaign_name || (c.direction === "inbound" ? "Inbound calls" : "No campaign");
        let g = map.get(id);
        if (!g) {
            g = { id, name, calls: [], completed: 0, failed: 0, totalDuration: 0, latest: 0 };
            map.set(id, g);
        }
        g.calls.push(c);
        const classification = classifyCall(c);
        if (classification.answered) g.completed++;
        if (classification.failed) g.failed++;
        if (c.duration_seconds) g.totalDuration += c.duration_seconds;
        const ts = new Date(c.created_at).getTime();
        if (Number.isFinite(ts) && ts > g.latest) g.latest = ts;
    }
    for (const g of map.values()) {
        g.calls.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
    }
    return Array.from(map.values()).sort((a, b) => b.latest - a.latest);
}

function CampaignSection({ group, defaultOpen, canPlayMedia }: { group: CampaignGroup; defaultOpen: boolean; canPlayMedia: boolean }) {
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
                className="flex w-full flex-col items-stretch gap-3 text-left sm:flex-row sm:items-center sm:justify-between"
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
                <div className="flex shrink-0 flex-wrap items-center gap-2 sm:justify-end">
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
                        <div className={`mt-4 hidden ${DESKTOP_CALL_GRID} gap-3 px-4 pb-2 text-left text-xs font-semibold uppercase tracking-wider text-muted-foreground xl:grid`}>
                            <div>Phone</div>
                            <div>Status</div>
                            <div>Outcome</div>
                            <div>Duration</div>
                            <div>Date</div>
                            <div className="text-center">AI</div>
                            <div className="text-center">Script</div>
                            <div aria-hidden />
                            <div aria-hidden />
                            <div aria-hidden />
                            <div aria-hidden />
                        </div>
                        <div className="space-y-2">
                            {group.calls.map((call) => (
                                <CallRow key={call.id} call={call} canPlayMedia={canPlayMedia} />
                            ))}
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>
        </motion.section>
    );
}

export default function CallsPage() {
    const permissions = useEffectivePermissions();
    const recordingCapabilities = getRecordingCapabilities(
        permissions.isSuccess ? permissions.data.permissions : undefined,
    );
    const canPlayMedia = permissions.isSuccess && recordingCapabilities.canRead;
    const [page, setPage] = useState(1);
    const pageSize = 50;
    const [search, setSearch] = useState("");
    const [filter, setFilter] = useState<"all" | "leads" | "issues">("all");
    const [direction, setDirection] = useState<"all" | "inbound" | "outbound">("all");
    const [inboundCampaignId, setInboundCampaignId] = useState<string | undefined>();

    useEffect(() => {
        const timer = window.setTimeout(() => {
            const params = new URLSearchParams(window.location.search);
            const requestedDirection = params.get("direction");
            if (requestedDirection === "inbound" || requestedDirection === "outbound") setDirection(requestedDirection);
            setInboundCampaignId(params.get("inbound_campaign_id") || undefined);
        }, 0);
        return () => window.clearTimeout(timer);
    }, []);

    const q = useCalls(page, pageSize, { direction: direction === "all" ? undefined : direction, inboundCampaignId });
    const allCalls = useMemo(() => q.data?.calls ?? [], [q.data]);
    const calls = useMemo(() => {
        const term = search.trim().toLowerCase();
        return allCalls.filter((c) => {
            if (direction !== "all" && c.direction !== direction) return false;
            if (inboundCampaignId && c.inbound_campaign_id !== inboundCampaignId) return false;
            if (term && ![c.phone_number, c.from_number, c.to_number, c.campaign_name].some((entry) => (entry || "").toLowerCase().includes(term))) return false;
            const v = (c.lead_outcome || "").toLowerCase();
            if (filter === "leads") return v.startsWith("qualified") || v.startsWith("callback");
            if (filter === "issues") {
                return classifyCall(c).failed || v.startsWith("no_interest") || v.startsWith("disqualified");
            }
            return true;
        });
    }, [allCalls, search, filter, direction, inboundCampaignId]);
    const total = q.data?.total ?? 0;
    const error = q.isError ? (q.error instanceof Error ? q.error.message : "Failed to load calls") : "";
    const groups = useMemo(() => groupByCampaign(calls), [calls]);
    const totalPages = Math.ceil(total / pageSize);
    const hasActiveFilters = Boolean(search.trim()) || filter !== "all" || direction !== "all" || Boolean(inboundCampaignId);

    return (
        <DashboardLayout title="Call History" description="Inbound and outbound calls with direction-aware parties, transcripts, and media">
            <CallIssuesBanner />
            {q.isLoading ? (
                <div role="status" aria-label="Loading call history" className="flex h-64 items-center justify-center">
                    <div aria-hidden className="h-8 w-8 animate-spin rounded-full border-b-2 border-foreground/60" />
                </div>
            ) : error ? (
                <div role="alert" className="content-card border-destructive/30 text-destructive">{error}</div>
            ) : allCalls.length === 0 && !hasActiveFilters ? (
                <motion.div
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="content-card py-16 text-center"
                >
                    <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-muted/40">
                        <Phone className="h-8 w-8 text-muted-foreground" />
                    </div>
                    <h3 className="mb-2 text-lg font-semibold text-foreground">No calls yet</h3>
                    <p className="text-muted-foreground">Calls will appear here after an outbound campaign starts or an active inbound line receives a call.</p>
                </motion.div>
            ) : (
                <div className="space-y-4">
                    {/* Search + filter toolbar — client-side over the loaded page */}
                    <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                        <div className="relative w-full sm:max-w-xs">
                            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                            <label htmlFor="call-history-search" className="sr-only">Search calls by phone number, DID, or campaign</label>
                            <input
                                id="call-history-search"
                                type="text"
                                value={search}
                                onChange={(e) => { setSearch(e.target.value); setPage(1); }}
                                placeholder="Search by phone number…"
                                className="w-full rounded-lg border border-border bg-background py-2 pl-9 pr-3 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring/40"
                            />
                        </div>
                        <div className="flex max-w-full flex-wrap items-center gap-2">
                        <div className="flex items-center gap-1 rounded-lg border border-border bg-background p-1" role="group" aria-label="Filter calls by direction">
                            {(["all", "inbound", "outbound"] as const).map((key) => <button key={key} type="button" aria-pressed={direction === key} onClick={() => { setDirection(key); setPage(1); }} className={`rounded-md px-3 py-1 text-xs font-semibold capitalize transition-colors ${direction === key ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"}`}>{key}</button>)}
                        </div>
                        <div className="flex items-center gap-1 rounded-lg border border-border bg-background p-1" role="group" aria-label="Filter calls by outcome">
                            {([
                                ["all", "All"],
                                ["leads", "Leads"],
                                ["issues", "Issues"],
                            ] as const).map(([key, label]) => (
                                <button
                                    key={key}
                                    type="button"
                                    aria-pressed={filter === key}
                                    onClick={() => { setFilter(key); setPage(1); }}
                                    className={`rounded-md px-3 py-1 text-xs font-semibold transition-colors duration-150 ease-out ${
                                        filter === key
                                            ? "bg-accent text-accent-foreground"
                                            : "text-muted-foreground hover:text-foreground"
                                    }`}
                                >
                                    {label}
                                </button>
                            ))}
                        </div>
                        </div>
                    </div>

                    {calls.length === 0 ? (
                        <div className="content-card py-12 text-center">
                            <p className="text-muted-foreground">No calls match your search or filter.</p>
                            <button
                                type="button"
                                onClick={() => {
                                    setSearch("");
                                    setFilter("all");
                                    setDirection("all");
                                    setInboundCampaignId(undefined);
                                    setPage(1);
                                }}
                                className="mt-3 text-sm font-semibold text-foreground underline-offset-4 hover:underline"
                            >
                                Clear filters
                            </button>
                        </div>
                    ) : (
                        groups.map((g, idx) => (
                            <CampaignSection key={g.id} group={g} defaultOpen={idx === 0} canPlayMedia={canPlayMedia} />
                        ))
                    )}

                    {totalPages > 1 && (
                        <motion.div
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            transition={{ delay: 0.3 }}
                            className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"
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
