"use client";

import { useCallback, useEffect, useState } from "react";
import { Clock3, PhoneIncoming, RefreshCw, ShieldAlert } from "lucide-react";

import { api, type RejectedInboundCallItem } from "@/lib/api";

const POLL_INTERVAL_MS = 15_000;
const PAGE_SIZE = 25;

const REASON_LABELS: Record<string, string> = {
    after_hours_closed: "After hours",
    unknown_did: "Unknown number",
    tenant_conflict: "Number routing conflict",
    did_not_verified: "Number not verified",
    trunk_not_ready: "Phone trunk unavailable",
    concurrency_policy_missing: "Capacity policy unavailable",
    admission_timeout: "Admission timed out",
    max_active_calls_reached: "All lines in use",
    subscription_inactive: "Subscription inactive",
    tenant_inbound_disabled: "Inbound disabled",
    insufficient_minutes: "Insufficient minutes",
};

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

export function RejectedInboundCallsPanel({ campaignId }: RejectedInboundCallsPanelProps) {
    const [items, setItems] = useState<RejectedInboundCallItem[]>([]);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async (background = false) => {
        if (background) setRefreshing(true);
        try {
            const response = await api.listRejectedInboundCalls({
                campaignId,
                page: 1,
                pageSize: PAGE_SIZE,
            });
            setItems(response.items);
            setTotal(response.total);
            setError(null);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load rejected inbound calls");
        } finally {
            setLoading(false);
            setRefreshing(false);
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
                <div className="px-5 py-4 text-sm text-red-600 dark:text-red-400" role="alert">{error}</div>
            ) : loading ? (
                <div className="px-5 py-8 text-center text-sm text-muted-foreground">Loading rejected calls…</div>
            ) : items.length === 0 ? (
                <div className="px-5 py-8 text-center">
                    <PhoneIncoming className="mx-auto mb-2 h-5 w-5 text-emerald-500" aria-hidden />
                    <p className="text-sm font-medium text-gray-900 dark:text-zinc-100">No rejected inbound calls</p>
                    <p className="mt-1 text-xs text-muted-foreground">Denied and after-hours attempts will appear here.</p>
                </div>
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
