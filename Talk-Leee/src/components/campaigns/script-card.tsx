"use client";

import { useCallback, useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ChevronDown, ChevronRight, Loader2, MessageSquare, Phone } from "lucide-react";
import {
    extendedApi,
    CampaignCallWithTranscript,
    TranscriptTurn,
} from "@/lib/extended-api";

interface ScriptCardProps {
    campaignId: string;
}

const PAGE_SIZE = 20;

function formatTimestamp(iso: string): string {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatStartedAt(iso: string | null): string {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString();
}

function formatDuration(seconds: number | null): string {
    if (seconds == null) return "--";
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function OutcomeBadge({ outcome }: { outcome: string | null }) {
    const style =
        outcome === "goal_achieved"
            ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/20"
            : outcome === "failed"
            ? "bg-red-500/10 text-red-700 dark:text-red-400 border-red-500/20"
            : "bg-muted text-muted-foreground border-border";
    return (
        <span className={`px-2 py-0.5 text-xs font-medium rounded-full border ${style}`}>
            {outcome ?? "--"}
        </span>
    );
}

function TranscriptTurnRow({ turn }: { turn: TranscriptTurn }) {
    const isUser = turn.role === "user";
    return (
        <div className={`flex gap-3 py-2 ${isUser ? "" : "pl-6"}`}>
            <div className="flex-shrink-0 text-xs font-mono text-muted-foreground w-20 pt-0.5">
                {formatTimestamp(turn.timestamp)}
            </div>
            <div
                className={`flex-shrink-0 text-xs font-semibold uppercase tracking-wide w-16 pt-0.5 ${
                    isUser ? "text-sky-600 dark:text-sky-400" : "text-emerald-600 dark:text-emerald-400"
                }`}
            >
                {isUser ? "Caller" : "Agent"}
            </div>
            <div className="text-sm text-foreground whitespace-pre-wrap break-words">
                {turn.content}
            </div>
        </div>
    );
}

function CallRow({ call }: { call: CampaignCallWithTranscript }) {
    const [expanded, setExpanded] = useState(false);
    const turnCount = call.turns.length;

    return (
        <div className="border border-border rounded-lg overflow-hidden">
            <button
                onClick={() => setExpanded((e) => !e)}
                className="w-full flex items-center gap-3 p-3 text-left hover:bg-muted/30 transition-colors"
            >
                {expanded ? (
                    <ChevronDown className="w-4 h-4 flex-shrink-0 text-muted-foreground" />
                ) : (
                    <ChevronRight className="w-4 h-4 flex-shrink-0 text-muted-foreground" />
                )}
                <Phone className="w-4 h-4 flex-shrink-0 text-muted-foreground" />
                <span className="text-sm font-mono text-foreground flex-shrink-0 tabular-nums">
                    {call.to_number || "--"}
                </span>
                <span className="text-xs text-muted-foreground flex-shrink-0 tabular-nums">
                    {formatStartedAt(call.started_at)}
                </span>
                <span className="text-xs text-muted-foreground flex-shrink-0 tabular-nums">
                    {formatDuration(call.duration_seconds)}
                </span>
                <OutcomeBadge outcome={call.outcome} />
                <span className="ml-auto text-xs text-muted-foreground flex items-center gap-1">
                    <MessageSquare className="w-3.5 h-3.5" />
                    {turnCount} {turnCount === 1 ? "turn" : "turns"}
                </span>
            </button>
            <AnimatePresence initial={false}>
                {expanded && (
                    <motion.div
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: "auto", opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        transition={{ duration: 0.18 }}
                        className="overflow-hidden border-t border-border bg-muted/20"
                    >
                        <div className="p-4">
                            {turnCount === 0 ? (
                                <div className="text-sm text-muted-foreground italic">
                                    No transcript available for this call.
                                </div>
                            ) : (
                                <div className="divide-y divide-border/50">
                                    {call.turns.map((turn, idx) => (
                                        <TranscriptTurnRow key={idx} turn={turn} />
                                    ))}
                                </div>
                            )}
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
}

export function ScriptCard({ campaignId }: ScriptCardProps) {
    const [items, setItems] = useState<CampaignCallWithTranscript[]>([]);
    const [page, setPage] = useState(1);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(
        async (nextPage: number) => {
            try {
                setLoading(true);
                setError(null);
                const resp = await extendedApi.getCampaignCallsWithTranscripts(
                    campaignId,
                    nextPage,
                    PAGE_SIZE
                );
                setItems(resp.items);
                setTotal(resp.total);
                setPage(resp.page);
            } catch (err) {
                setError(err instanceof Error ? err.message : "Failed to load transcripts");
            } finally {
                setLoading(false);
            }
        },
        [campaignId]
    );

    useEffect(() => {
        if (campaignId) void load(1);
    }, [campaignId, load]);

    const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

    return (
        <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.35 }}
            className="content-card"
        >
            <div className="flex items-center justify-between mb-4">
                <div>
                    <h3 className="text-lg font-semibold text-foreground">Script</h3>
                    <p className="text-sm text-muted-foreground">
                        Every call on this campaign — user speech and agent replies with timestamps.
                    </p>
                </div>
                <div className="text-sm text-muted-foreground tabular-nums">
                    {total} {total === 1 ? "call" : "calls"}
                </div>
            </div>

            {error && (
                <div className="mb-3 p-3 rounded-lg text-sm border bg-red-500/10 border-red-500/30 text-red-700 dark:text-red-400">
                    {error}
                </div>
            )}

            {loading && items.length === 0 ? (
                <div className="flex items-center justify-center py-10 text-muted-foreground">
                    <Loader2 className="w-5 h-5 animate-spin" />
                </div>
            ) : items.length === 0 ? (
                <div className="text-center py-8 text-muted-foreground">
                    No calls yet. Transcripts appear here as the campaign makes calls.
                </div>
            ) : (
                <div className="space-y-2">
                    {items.map((call) => (
                        <CallRow key={call.call_id} call={call} />
                    ))}
                </div>
            )}

            {pageCount > 1 && (
                <div className="flex items-center justify-between mt-4 pt-4 border-t border-border">
                    <button
                        className="text-sm text-muted-foreground hover:text-foreground disabled:opacity-40"
                        disabled={page <= 1 || loading}
                        onClick={() => load(page - 1)}
                    >
                        ← Previous
                    </button>
                    <span className="text-xs text-muted-foreground tabular-nums">
                        Page {page} of {pageCount}
                    </span>
                    <button
                        className="text-sm text-muted-foreground hover:text-foreground disabled:opacity-40"
                        disabled={page >= pageCount || loading}
                        onClick={() => load(page + 1)}
                    >
                        Next →
                    </button>
                </div>
            )}
        </motion.div>
    );
}
