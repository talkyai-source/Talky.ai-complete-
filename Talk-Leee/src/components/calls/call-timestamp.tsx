"use client";

import { Clock } from "lucide-react";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

export function formatCallDuration(seconds?: number | null): string {
    if (seconds === undefined || seconds === null || !Number.isFinite(seconds)) return "—";
    const total = Math.max(0, Math.round(seconds));
    const m = Math.floor(total / 60);
    const s = total % 60;
    return `${m}:${String(s).padStart(2, "0")}`;
}

/** The text the hover card shows; exported so the test can pin it. */
export function callTimestampLabel(iso: string): string {
    return new Date(iso).toLocaleString(undefined, { dateStyle: "full", timeStyle: "medium" });
}

/**
 * Call History time cell (2026-09-08): a clock icon only, sitting right before
 * the AI summary. The date and time are NOT printed in the row — the owner
 * wanted a quiet row — they appear on hover/focus together with the duration
 * and the viewer's timezone. Screen readers get the full time via aria-label.
 */
export function CallTimestamp({ iso, durationSeconds }: { iso: string; durationSeconds?: number | null }) {
    const label = callTimestampLabel(iso);
    return (
        <TooltipProvider delayDuration={150}>
            <Tooltip>
                <TooltipTrigger asChild>
                    <button
                        type="button"
                        aria-label={`Call time ${label}`}
                        data-testid="call-timestamp"
                        className="inline-flex h-8 w-8 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-foreground/5 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                        <Clock className="h-4 w-4" aria-hidden />
                        <time dateTime={iso} className="sr-only">{label}</time>
                    </button>
                </TooltipTrigger>
                <TooltipContent side="top" align="center" sideOffset={8} className="p-3 text-xs shadow-xl">
                    <div className="font-semibold">{label}</div>
                    <div className="mt-1 text-muted-foreground">
                        Duration {formatCallDuration(durationSeconds)} · {Intl.DateTimeFormat().resolvedOptions().timeZone}
                    </div>
                </TooltipContent>
            </Tooltip>
        </TooltipProvider>
    );
}
