"use client";

/**
 * Call Issues panel — explains why calls AREN'T going through.
 *
 * The live-calls panel only shows rows that made it into the `calls` table.
 * But most things that stop a call (out of minutes, outside calling hours,
 * campaign stopped, caller-ID not verified, voice-provider/TTS failure, rate
 * limits) happen in the dialer BEFORE a call row exists — so the operator was
 * blind to them. This polls GET /calls/issues live (4s).
 *
 * Smart rendering, not a wall of cards:
 *   - issues are GROUPED by type (reason) — one compact row per problem,
 *     with a count of affected numbers, not one card per phone number;
 *   - collapsed by default to a single slim summary bar; expand to see
 *     each problem, expand a problem to see its numbers;
 *   - self-clearing pacing states (call gap, batch slots, rate limiter)
 *     never appear — the backend excludes them as normal operation.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import {
    AlertTriangle, AlertCircle, Info, Clock, ChevronDown, ChevronRight, RefreshCw,
} from "lucide-react";

import { api, type CallIssue } from "@/lib/api";

const POLL_INTERVAL_MS = 4000;

type SeverityLook = {
    Icon: typeof AlertCircle;
    iconColor: string;
    titleColor: string;
};

function severityLook(severity: string): SeverityLook {
    switch (severity) {
        case "error":
            return {
                Icon: AlertCircle,
                iconColor: "text-red-500",
                titleColor: "text-red-600 dark:text-red-400",
            };
        case "warning":
            return {
                Icon: AlertTriangle,
                iconColor: "text-amber-500",
                titleColor: "text-amber-700 dark:text-amber-400",
            };
        default:
            return {
                Icon: Info,
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

type IssueGroup = {
    key: string;
    title: string;
    suggestion: string;
    severity: string;
    items: CallIssue[];
    latestAt: string | null;
};

function groupIssues(items: CallIssue[]): IssueGroup[] {
    const map = new Map<string, IssueGroup>();
    for (const it of items) {
        const key = it.reason_code || it.title;
        const g = map.get(key);
        if (g) {
            g.items.push(it);
            if (it.updated_at && (!g.latestAt || it.updated_at > g.latestAt)) g.latestAt = it.updated_at;
        } else {
            map.set(key, {
                key,
                title: it.title,
                suggestion: it.suggestion,
                severity: it.severity,
                items: [it],
                latestAt: it.updated_at ?? null,
            });
        }
    }
    // Errors first, then warnings, then info; biggest groups first within a tier.
    const rank = (s: string) => (s === "error" ? 0 : s === "warning" ? 1 : 2);
    return [...map.values()].sort(
        (a, b) => rank(a.severity) - rank(b.severity) || b.items.length - a.items.length,
    );
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
    const [open, setOpen] = useState(false);
    const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});
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

    const groups = useMemo(() => groupIssues(items), [items]);
    const worst = groups[0]?.severity ?? "info";

    // Nothing wrong → render nothing at all. A healthy campaign gets its
    // space back (this also auto-clears the moment the backend stops
    // reporting an issue — the panel is live, not sticky).
    if (loaded && items.length === 0 && !error) return null;
    if (!loaded && items.length === 0) return null;

    const headLook = severityLook(worst);

    return (
        <div className="rounded-2xl border border-amber-200/60 dark:border-amber-500/20 bg-white dark:bg-white/5 shadow-sm overflow-hidden">
            {/* Slim, always-one-line summary bar. Click to expand. */}
            <button
                type="button"
                onClick={() => setOpen((v) => !v)}
                className="w-full flex items-center justify-between px-4 py-2.5 text-left"
                aria-expanded={open}
            >
                <div className="flex items-center gap-2 min-w-0">
                    {open ? (
                        <ChevronDown className="h-3.5 w-3.5 text-muted-foreground shrink-0" aria-hidden />
                    ) : (
                        <ChevronRight className="h-3.5 w-3.5 text-muted-foreground shrink-0" aria-hidden />
                    )}
                    <headLook.Icon className={`h-4 w-4 shrink-0 ${headLook.iconColor}`} aria-hidden />
                    <h3 className="text-sm font-semibold text-gray-900 dark:text-zinc-100">{title}</h3>
                    <span className="text-xs text-muted-foreground truncate">
                        {groups.length === 1
                            ? `${groups[0].title} · ${groups[0].items.length} ${groups[0].items.length === 1 ? "number" : "numbers"}`
                            : `${groups.length} problems · ${items.length} numbers`}
                    </span>
                </div>
                {error ? (
                    <span className="text-xs text-red-600 dark:text-red-400 truncate max-w-[40%]" title={error}>
                        {error}
                    </span>
                ) : (
                    <RefreshCw className="h-3.5 w-3.5 text-muted-foreground/40 shrink-0" aria-hidden />
                )}
            </button>

            {open && (
                <div className="divide-y divide-gray-100 dark:divide-white/5 border-t border-gray-100 dark:border-white/5 max-h-72 overflow-y-auto">
                    {groups.map((g) => {
                        const look = severityLook(g.severity);
                        const isOpen = openGroups[g.key] ?? false;
                        return (
                            <div key={g.key} className="px-4 py-2.5">
                                <button
                                    type="button"
                                    onClick={() => setOpenGroups((m) => ({ ...m, [g.key]: !isOpen }))}
                                    className="w-full flex items-start gap-2.5 text-left"
                                    aria-expanded={isOpen}
                                >
                                    <look.Icon className={`h-4 w-4 mt-0.5 shrink-0 ${look.iconColor}`} aria-hidden />
                                    <div className="min-w-0 flex-1">
                                        <div className="flex items-center gap-2 flex-wrap">
                                            <span className={`text-sm font-semibold ${look.titleColor}`}>{g.title}</span>
                                            <span className="text-xs rounded-full bg-gray-100 dark:bg-white/10 px-2 py-0.5 text-muted-foreground">
                                                {g.items.length} {g.items.length === 1 ? "number" : "numbers"}
                                            </span>
                                            {g.latestAt && (
                                                <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground/70">
                                                    <Clock className="h-3 w-3" aria-hidden />
                                                    {fmtTime(g.latestAt)}
                                                </span>
                                            )}
                                        </div>
                                        <p className="mt-0.5 text-xs text-muted-foreground">{g.suggestion}</p>
                                    </div>
                                    {isOpen ? (
                                        <ChevronDown className="h-3.5 w-3.5 mt-1 text-muted-foreground shrink-0" aria-hidden />
                                    ) : (
                                        <ChevronRight className="h-3.5 w-3.5 mt-1 text-muted-foreground shrink-0" aria-hidden />
                                    )}
                                </button>
                                {isOpen && (
                                    <div className="mt-2 ml-6 flex flex-wrap gap-1.5">
                                        {g.items.map((it) => (
                                            <span
                                                key={it.job_id}
                                                className="text-[11px] font-mono rounded-md bg-gray-50 dark:bg-white/5 border border-gray-200 dark:border-white/10 px-1.5 py-0.5 text-muted-foreground"
                                                title={`${it.title} · attempt ${it.attempts}${it.updated_at ? ` · ${fmtTime(it.updated_at)}` : ""}`}
                                            >
                                                {it.phone_number}
                                            </span>
                                        ))}
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
}

export default CallIssuesPanel;
