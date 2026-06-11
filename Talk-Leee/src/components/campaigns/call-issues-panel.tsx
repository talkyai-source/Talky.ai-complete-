"use client";

/**
 * Call Issues panel — explains why calls AREN'T going through.
 *
 * The live-calls panel only shows rows that made it into the `calls` table.
 * But most things that stop a call (out of minutes, outside calling hours,
 * campaign stopped, caller-ID not verified, voice-provider/TTS failure, rate
 * limits) happen in the dialer BEFORE a call row exists — so the operator was
 * blind to them. This polls GET /calls/issues and renders one card per stuck
 * number with a plain-English title and an actionable suggestion.
 */

import { useEffect, useRef, useState } from "react";
import {
    AlertTriangle, AlertCircle, Info, Clock, Phone, RefreshCw,
} from "lucide-react";

import { api, type CallIssue } from "@/lib/api";

const POLL_INTERVAL_MS = 4000;

type SeverityLook = {
    Icon: typeof AlertCircle;
    card: string;
    iconColor: string;
    titleColor: string;
};

function severityLook(severity: string): SeverityLook {
    switch (severity) {
        case "error":
            return {
                Icon: AlertCircle,
                card: "border-red-500/30 bg-red-500/5",
                iconColor: "text-red-500",
                titleColor: "text-red-600 dark:text-red-400",
            };
        case "warning":
            return {
                Icon: AlertTriangle,
                card: "border-amber-500/30 bg-amber-500/5",
                iconColor: "text-amber-500",
                titleColor: "text-amber-700 dark:text-amber-400",
            };
        default:
            return {
                Icon: Info,
                card: "border-sky-500/30 bg-sky-500/5",
                iconColor: "text-sky-500",
                titleColor: "text-sky-700 dark:text-sky-400",
            };
    }
}

function fmtTime(iso?: string | null): string {
    if (!iso) return "";
    const t = Date.parse(iso);
    if (Number.isNaN(t)) return "";
    return new Date(t).toLocaleTimeString();
}

export type CallIssuesPanelProps = {
    /** Scope to one campaign. Omit for tenant-wide. */
    campaignId?: string;
    title?: string;
};

export function CallIssuesPanel({ campaignId, title = "Call issues" }: CallIssuesPanelProps) {
    const [items, setItems] = useState<CallIssue[]>([]);
    const [loaded, setLoaded] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const aborted = useRef(false);

    useEffect(() => {
        aborted.current = false;
        let timer: number | undefined;

        const poll = async () => {
            try {
                const res = await api.listCallIssues({ campaignId });
                if (aborted.current) return;
                setItems(res.items);
                setError(null);
            } catch (err) {
                if (aborted.current) return;
                setError(err instanceof Error ? err.message : "Failed to load call issues");
            } finally {
                if (!aborted.current) {
                    setLoaded(true);
                    timer = window.setTimeout(poll, POLL_INTERVAL_MS);
                }
            }
        };

        void poll();
        return () => {
            aborted.current = true;
            if (timer !== undefined) window.clearTimeout(timer);
        };
    }, [campaignId]);

    // Hide the whole card when there's nothing wrong — no clutter on a healthy
    // campaign. (Only after the first load so it doesn't flicker in/out.)
    if (loaded && items.length === 0 && !error) return null;

    return (
        <div className="rounded-2xl border border-amber-200/60 dark:border-amber-500/20 bg-white dark:bg-white/5 shadow-sm overflow-hidden">
            <div className="flex items-center justify-between px-4 py-3 border-b border-amber-200/60 dark:border-amber-500/20">
                <div className="flex items-center gap-2">
                    <AlertTriangle className="h-4 w-4 text-amber-500" />
                    <h3 className="text-sm font-semibold text-gray-900 dark:text-zinc-100">{title}</h3>
                    {items.length > 0 && (
                        <span className="text-xs text-muted-foreground">
                            {items.length} {items.length === 1 ? "call needs attention" : "calls need attention"}
                        </span>
                    )}
                </div>
                {error ? (
                    <span className="text-xs text-red-600 dark:text-red-400 truncate max-w-[40%]" title={error}>
                        {error}
                    </span>
                ) : (
                    <RefreshCw className="h-3.5 w-3.5 text-muted-foreground/50" aria-hidden />
                )}
            </div>

            <div className="divide-y divide-gray-100 dark:divide-white/5">
                {items.map((it) => {
                    const look = severityLook(it.severity);
                    return (
                        <div key={it.job_id} className={`px-4 py-3 border-l-2 ${look.card}`}>
                            <div className="flex items-start gap-3">
                                <look.Icon className={`h-4 w-4 mt-0.5 shrink-0 ${look.iconColor}`} aria-hidden />
                                <div className="min-w-0 flex-1">
                                    <div className="flex items-center gap-2 flex-wrap">
                                        <span className={`text-sm font-semibold ${look.titleColor}`}>
                                            {it.title}
                                        </span>
                                        <span className="inline-flex items-center gap-1 text-xs font-mono text-muted-foreground">
                                            <Phone className="h-3 w-3" aria-hidden />
                                            {it.phone_number}
                                        </span>
                                        {it.attempts > 1 && (
                                            <span className="text-[11px] text-muted-foreground">
                                                · attempt {it.attempts}
                                            </span>
                                        )}
                                    </div>
                                    <p className="mt-1 text-sm text-muted-foreground">{it.suggestion}</p>
                                    <div className="mt-1 flex items-center gap-3 text-[11px] text-muted-foreground/70">
                                        {it.updated_at && (
                                            <span className="inline-flex items-center gap-1">
                                                <Clock className="h-3 w-3" aria-hidden />
                                                {fmtTime(it.updated_at)}
                                            </span>
                                        )}
                                        {it.reason_code && (
                                            <span className="font-mono truncate max-w-[60%]" title={it.reason_code}>
                                                {it.reason_code}
                                            </span>
                                        )}
                                    </div>
                                </div>
                            </div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

export default CallIssuesPanel;
