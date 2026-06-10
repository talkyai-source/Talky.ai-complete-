"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, X } from "lucide-react";
import { extendedApi } from "@/lib/extended-api";

type Issue = {
    id: string;
    title: string;
    description?: string | null;
};

/**
 * Surfaces recent critical call-pipeline failures (category="call",
 * severity="critical") — e.g. "Text-to-speech out of credits" — as a
 * dismissible red banner. Polls every 60s. Renders nothing when there are no
 * (undismissed) issues. Mount at the top of the dashboard / campaign pages.
 */
export function CallIssuesBanner() {
    const [issues, setIssues] = useState<Issue[]>([]);
    const [dismissed, setDismissed] = useState<Set<string>>(new Set());

    useEffect(() => {
        let stopped = false;
        const load = async () => {
            try {
                const res = await extendedApi.getRecentCallIssues();
                if (!stopped) setIssues(res.items ?? []);
            } catch {
                // best-effort — a failing banner must never break the page
            }
        };
        load();
        const id = window.setInterval(load, 60_000);
        return () => {
            stopped = true;
            window.clearInterval(id);
        };
    }, []);

    const visible = issues.filter((i) => !dismissed.has(i.id));
    if (visible.length === 0) return null;
    const top = visible[0];
    const extra = visible.length - 1;

    return (
        <div className="mb-4 flex items-start gap-3 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm">
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-red-600 dark:text-red-400" />
            <div className="min-w-0 flex-1">
                <div className="font-semibold text-red-800 dark:text-red-300">{top.title}</div>
                {top.description ? (
                    <div className="text-red-700/90 dark:text-red-300/80">{top.description}</div>
                ) : null}
                {extra > 0 ? (
                    <div className="mt-0.5 text-xs text-red-700/70 dark:text-red-300/60">
                        +{extra} more call issue{extra > 1 ? "s" : ""}
                    </div>
                ) : null}
            </div>
            <button
                type="button"
                onClick={() => setDismissed((d) => new Set(d).add(top.id))}
                aria-label="Dismiss"
                className="shrink-0 text-red-700/70 transition-colors hover:text-red-800 dark:text-red-300/70 dark:hover:text-red-200"
            >
                <X className="h-4 w-4" />
            </button>
        </div>
    );
}
