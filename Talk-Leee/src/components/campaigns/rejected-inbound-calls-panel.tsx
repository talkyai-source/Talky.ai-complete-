"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Clock3, RefreshCw, ShieldAlert } from "lucide-react";

import { EmptyState, ErrorState, LoadingState, failedReadMessage } from "@/components/states/page-states";
import { api, type RejectedInboundCallItem } from "@/lib/api";
import { REASON_LABELS } from "@/lib/inbound-reason-labels";

const POLL_INTERVAL_MS = 15_000;
const PAGE_SIZE = 25;

function reasonLabel(reason: string): string {
    return REASON_LABELS[reason] ?? reason.replaceAll("_", " ");
}

function occurredAtLabel(value: string): string {
    const timestamp = Date.parse(value);
    if (Number.isNaN(timestamp)) return value;
    return new Date(timestamp).toLocaleString();
}

export type RejectedInboundCallsPanelProps = {
    campaignId?: string;
};

export function RejectedInboundCallsPanel(props: RejectedInboundCallsPanelProps) {
    return <RejectedInboundCallsPanelScope key={props.campaignId ?? "all"} {...props} />;
}

function RejectedInboundCallsPanelScope({ campaignId }: RejectedInboundCallsPanelProps) {
    const [items, setItems] = useState<RejectedInboundCallItem[]>([]);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const requestGeneration = useRef(0);

    const load = useCallback(async (background = false) => {
        const generation = ++requestGeneration.current;
        if (background) setRefreshing(true);
        else setLoading(true);
        try {
            const response = await api.listRejectedInboundCalls({
                campaignId,
                page: 1,
                pageSize: PAGE_SIZE,
            });
            if (generation !== requestGeneration.current) return;
            // An answer without its list is a failed read, not an empty one.
            if (!Array.isArray(response?.items)) throw new Error();
            setItems(response.items);
            setTotal(response.total);
            setError(null);
        } catch (err) {
            if (generation !== requestGeneration.current) return;
            setError(failedReadMessage(err, "Failed to load rejected inbound calls"));
        } finally {
            if (generation === requestGeneration.current) {
                setLoading(false);
                setRefreshing(false);
            }
        }
    }, [campaignId]);

    useEffect(() => {
        let cancelled = false;
        let timer: number | undefined;

        const poll = async (background = true) => {
            if (cancelled) return;
            await load(background);
            if (!cancelled) timer = window.setTimeout(poll, POLL_INTERVAL_MS);
        };

        timer = window.setTimeout(() => void poll(false), 0);
        return () => {
            cancelled = true;
            requestGeneration.current += 1;
            if (timer !== undefined) window.clearTimeout(timer);
        };
    }, [load]);

    return (
        <section className="rounded-2xl border border-gray-200 dark:border-white/10 bg-white dark:bg-white/5 shadow-sm overflow-hidden">
            <header className="flex items-center justify-between gap-4 px-5 py-4 border-b border-gray-100 dark:border-white/5">
                <div className="flex items-center gap-3 min-w-0">
                    <span className="grid h-9 w-9 place-items-center rounded-xl bg-amber-500/10 text-amber-600 dark:text-amber-400">
                        <ShieldAlert className="h-4 w-4" aria-hidden />
                    </span>
                    <div className="min-w-0">
                        <h3 className="text-sm font-semibold text-gray-900 dark:text-zinc-100">Rejected inbound calls</h3>
                        <p className="text-xs text-muted-foreground">
                            {loading ? "Loading history…" : `${total} denied or after-hours ${total === 1 ? "call" : "calls"}`}
                        </p>
                    </div>
                </div>
                <button
                    type="button"
                    onClick={() => void load(true)}
                    disabled={refreshing}
                    className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-gray-200 dark:border-white/10 px-2.5 text-xs font-medium text-muted-foreground hover:bg-gray-50 dark:hover:bg-white/5 disabled:opacity-50"
                    aria-label="Refresh rejected inbound calls"
                >
                    <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} aria-hidden />
                    Refresh
                </button>
            </header>

            {error ? (
                <div className="p-4"><ErrorState message={error} onRetry={() => void load()} /></div>
            ) : loading ? (
                <div className="p-4"><LoadingState title="Loading rejected calls…" /></div>
            ) : items.length === 0 ? (
                <div className="p-4"><EmptyState title="No rejected inbound calls" message="Denied and after-hours attempts will appear here." /></div>
            ) : (
                <div className="overflow-x-auto">
                    <table className="w-full text-left text-sm">
                        <thead className="bg-gray-50/80 dark:bg-white/[0.03] text-xs text-muted-foreground">
                            <tr>
                                <th className="px-5 py-2.5 font-medium">When</th>
                                <th className="px-4 py-2.5 font-medium">From</th>
                                <th className="px-4 py-2.5 font-medium">To</th>
                                <th className="px-4 py-2.5 font-medium">Result</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-100 dark:divide-white/5">
                            {items.map((item) => (
                                <tr key={`${item.source}:${item.id}`}>
                                    <td className="whitespace-nowrap px-5 py-3 text-xs text-muted-foreground">
                                        <span className="inline-flex items-center gap-1.5">
                                            <Clock3 className="h-3.5 w-3.5" aria-hidden />
                                            {occurredAtLabel(item.occurred_at)}
                                        </span>
                                    </td>
                                    <td className="whitespace-nowrap px-4 py-3 font-mono text-xs">{item.caller_ani || "Private / unavailable"}</td>
                                    <td className="whitespace-nowrap px-4 py-3 font-mono text-xs">{item.called_did || "Unknown"}</td>
                                    <td className="px-4 py-3">
                                        <span className={`inline-flex rounded-full px-2 py-1 text-xs font-medium ${
                                            item.status === "after_hours"
                                                ? "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300"
                                                : "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300"
                                        }`}>
                                            {reasonLabel(item.reason)}
                                        </span>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </section>
    );
}

export default RejectedInboundCallsPanel;
