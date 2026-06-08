"use client";

/**
 * Live calls panel — the "wallboard" view of what's happening right now.
 *
 * Polls GET /api/v1/calls/live every 2 seconds and renders a table of
 * in-flight calls (queued → dialing → ringing → answered → in_call → ended).
 * Calls that have ended stay visible for 60 s so the operator sees the
 * outcome before the row vanishes.
 *
 * Deliberately uses polling instead of SSE/WebSocket for v1:
 *   - one less moving piece on infra (no Redis pubsub),
 *   - works through any load balancer / CDN / cookie boundary,
 *   - 2 s feels live enough for operator-facing dashboards.
 * If we later need <500 ms updates (whisper mode, listen-in), switch to
 * an SSE subscriber on top of the same backend events table.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { Phone, PhoneCall, PhoneOff, PhoneIncoming, CircleCheck, CircleX, Loader2 } from "lucide-react";

import { api } from "@/lib/api";

const POLL_INTERVAL_MS = 1500;
const RECENT_WINDOW_SECONDS = 60;

type LiveCall = Awaited<ReturnType<typeof api.listLiveCalls>>["items"][number];

type StatusLook = {
    label: string;
    pillClass: string;
    Icon: typeof PhoneCall;
    iconClass: string;
    pulse?: boolean;
};

function statusLook(status: string, outcome?: string | null): StatusLook {
    // `outcome` only matters once status === ended.
    switch (status) {
        case "queued":
            return {
                label: "Queued",
                pillClass: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
                Icon: Loader2,
                iconClass: "animate-spin opacity-70",
            };
        case "dialing":
        case "initiated":
            return {
                label: "Dialing",
                pillClass: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300",
                Icon: PhoneCall,
                iconClass: "",
            };
        case "ringing":
            return {
                label: "Ringing",
                pillClass: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
                Icon: PhoneIncoming,
                iconClass: "animate-pulse",
                pulse: true,
            };
        case "answered":
        case "in_call":
            return {
                label: status === "in_call" ? "In call" : "Answered",
                pillClass: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
                Icon: PhoneCall,
                iconClass: "",
            };
        case "ended":
        case "completed":
        case "failed": {
            const isPositive = outcome === "answered" || outcome === "customer_hung_up" || outcome === "agent_hung_up";
            const isHard = outcome === "rejected" || outcome === "unreachable" || outcome === "network_failure" || outcome === "failed";
            return {
                label: outcome ? humanOutcome(outcome) : "Ended",
                pillClass: isPositive
                    ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300"
                    : isHard
                      ? "bg-red-50 text-red-700 dark:bg-red-950/60 dark:text-red-300"
                      : "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
                Icon: isPositive ? CircleCheck : isHard ? CircleX : PhoneOff,
                iconClass: "",
            };
        }
        default:
            return {
                label: status,
                pillClass: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
                Icon: Phone,
                iconClass: "",
            };
    }
}

function humanOutcome(outcome: string): string {
    switch (outcome) {
        case "answered": return "Answered";
        case "busy": return "Busy";
        case "no_answer": return "No answer";
        case "voicemail": return "Voicemail";
        case "rejected": return "Rejected";
        case "unreachable": return "Unreachable";
        case "network_failure": return "Network failure";
        case "cancelled": return "Cancelled";
        case "customer_hung_up": return "Customer hung up";
        case "agent_hung_up": return "Agent ended";
        case "failed": return "Failed";
        default: return outcome.replace(/_/g, " ");
    }
}

function fmtDuration(secs: number | null | undefined): string {
    if (secs === null || secs === undefined) return "—";
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    return `${m}:${String(s).padStart(2, "0")}`;
}

function elapsedSeconds(startIso: string | null | undefined, nowMs: number): number | null {
    if (!startIso) return null;
    const start = Date.parse(startIso);
    if (Number.isNaN(start)) return null;
    return Math.max(0, Math.floor((nowMs - start) / 1000));
}

export type LiveCallsPanelProps = {
    /** When set, scopes the panel to one campaign. Omit for tenant-wide view. */
    campaignId?: string;
    /** Optional title override. */
    title?: string;
};

export function LiveCallsPanel({ campaignId, title = "Live calls" }: LiveCallsPanelProps) {
    const [items, setItems] = useState<LiveCall[]>([]);
    const [error, setError] = useState<string | null>(null);
    // Tick every second so elapsed-time counters update between polls.
    const [nowMs, setNowMs] = useState<number>(() => Date.now());
    const [hangingUpId, setHangingUpId] = useState<string | null>(null);
    const aborted = useRef(false);

    async function handleHangup(callId: string) {
        try {
            setHangingUpId(callId);
            await api.hangupCall(callId);
            // Optimistic: mark ended locally so the row leaves "in flight"
            // instantly; the next poll reconciles with the server.
            setItems((prev) =>
                prev.map((it) =>
                    it.id === callId
                        ? { ...it, status: "ended", outcome: it.outcome ?? "agent_hung_up" }
                        : it,
                ),
            );
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to hang up call");
        } finally {
            setHangingUpId(null);
        }
    }

    useEffect(() => {
        aborted.current = false;
        let cancelTimer: number | undefined;

        const poll = async () => {
            try {
                const res = await api.listLiveCalls({
                    campaignId,
                    recentWindowSeconds: RECENT_WINDOW_SECONDS,
                });
                if (aborted.current) return;
                setItems(res.items);
                setError(null);
            } catch (err) {
                if (aborted.current) return;
                // Don't blank the panel on a transient error — just surface it.
                setError(err instanceof Error ? err.message : "Failed to load live calls");
            } finally {
                if (!aborted.current) {
                    cancelTimer = window.setTimeout(poll, POLL_INTERVAL_MS);
                }
            }
        };

        void poll();
        const tick = window.setInterval(() => setNowMs(Date.now()), 1000);

        return () => {
            aborted.current = true;
            if (cancelTimer !== undefined) window.clearTimeout(cancelTimer);
            window.clearInterval(tick);
        };
    }, [campaignId]);

    const live = useMemo(
        () => items.filter((it) => !["ended", "completed", "failed"].includes(it.status)),
        [items],
    );
    const recentlyEnded = useMemo(
        () => items.filter((it) => ["ended", "completed", "failed"].includes(it.status)),
        [items],
    );

    return (
        <div className="rounded-2xl border border-gray-200 dark:border-white/10 bg-white dark:bg-white/5 shadow-sm overflow-hidden">
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-white/10">
                <div className="flex items-center gap-2">
                    <span className="relative flex h-2.5 w-2.5">
                        <span className={`animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-70 ${live.length === 0 ? "hidden" : ""}`} />
                        <span className={`relative inline-flex rounded-full h-2.5 w-2.5 ${live.length > 0 ? "bg-emerald-500" : "bg-zinc-400"}`} />
                    </span>
                    <h3 className="text-sm font-semibold text-gray-900 dark:text-zinc-100">{title}</h3>
                    <span className="text-xs text-muted-foreground">
                        {live.length} in flight
                        {recentlyEnded.length > 0 ? ` · ${recentlyEnded.length} just ended` : ""}
                    </span>
                </div>
                {error && (
                    <span className="text-xs text-red-600 dark:text-red-400 truncate max-w-[40%]" title={error}>
                        {error}
                    </span>
                )}
            </div>

            {items.length === 0 ? (
                <div className="px-4 py-6 text-sm text-muted-foreground">
                    No calls in flight. Start the campaign to see live status here.
                </div>
            ) : (
                <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                        <thead className="text-xs uppercase text-muted-foreground bg-gray-50 dark:bg-white/5">
                            <tr>
                                <th className="px-4 py-2 text-left font-medium">To</th>
                                <th className="px-4 py-2 text-left font-medium">From</th>
                                <th className="px-4 py-2 text-left font-medium">Status</th>
                                <th className="px-4 py-2 text-left font-medium">Duration</th>
                                <th className="px-4 py-2 text-left font-medium">Started</th>
                                <th className="px-4 py-2 text-right font-medium">Action</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-200 dark:divide-white/10">
                            {items.map((c) => {
                                const look = statusLook(c.status, c.outcome);
                                const live = !["ended", "completed", "failed"].includes(c.status);
                                const elapsed = live
                                    ? elapsedSeconds(c.answered_at ?? c.started_at, nowMs)
                                    : c.duration_seconds ?? null;
                                return (
                                    <tr key={c.id} className="hover:bg-gray-50 dark:hover:bg-white/[0.04]">
                                        <td className="px-4 py-2 font-mono text-sm text-gray-900 dark:text-zinc-100">
                                            {c.to_number}
                                        </td>
                                        <td className="px-4 py-2 font-mono text-xs text-muted-foreground">
                                            {c.caller_id ?? "—"}
                                        </td>
                                        <td className="px-4 py-2">
                                            <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${look.pillClass}`}>
                                                <look.Icon className={`h-3.5 w-3.5 ${look.iconClass}`} aria-hidden />
                                                {look.label}
                                            </span>
                                        </td>
                                        <td className="px-4 py-2 font-mono text-sm tabular-nums">
                                            {fmtDuration(elapsed)}
                                        </td>
                                        <td className="px-4 py-2 text-xs text-muted-foreground">
                                            {c.started_at ? new Date(c.started_at).toLocaleTimeString() : "—"}
                                        </td>
                                        <td className="px-4 py-2 text-right">
                                            {live ? (
                                                <button
                                                    type="button"
                                                    onClick={() => handleHangup(c.id)}
                                                    disabled={hangingUpId === c.id}
                                                    aria-label={`Hang up call to ${c.to_number}`}
                                                    title="Hang up"
                                                    className="inline-flex h-7 w-7 items-center justify-center rounded-md text-red-600 transition-colors hover:bg-red-100 dark:text-red-400 dark:hover:bg-red-950/50 disabled:opacity-50"
                                                >
                                                    {hangingUpId === c.id ? (
                                                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                                    ) : (
                                                        <PhoneOff className="h-3.5 w-3.5" />
                                                    )}
                                                </button>
                                            ) : (
                                                <span className="text-muted-foreground">—</span>
                                            )}
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}

export default LiveCallsPanel;
