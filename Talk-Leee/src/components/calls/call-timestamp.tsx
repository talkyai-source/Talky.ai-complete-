"use client";

import { Clock } from "lucide-react";
import { formatTimestamp } from "@/lib/timestamps";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

export function formatCallDuration(seconds?: number | null): string {
    if (seconds === undefined || seconds === null || !Number.isFinite(seconds)) return "—";
    const total = Math.max(0, Math.round(seconds));
    const m = Math.floor(total / 60);
    const s = total % 60;
    return `${m}:${String(s).padStart(2, "0")}`;
}

/** The text the hover card shows; exported so the test can pin it. */
export function callTimestampLabel(iso: string, timeZone?: string): string {
    return formatTimestamp(iso, timeZone).label;
}

/**
 * Call History time cell (2026-09-08): a clock icon only, sitting right before
 * the AI summary. The date and time are NOT printed in the row — the owner
 * wanted a quiet row — they appear on hover/focus together with the duration
 * and the viewer's timezone. Screen readers get the full time via aria-label.
 */
export function CallTimestamp({ iso, durationSeconds, timeZone }: { iso: string; durationSeconds?: number | null; timeZone?: string }) {
    const stamp = formatTimestamp(iso, timeZone);
    const label = stamp.label;
    // Short visible form (2026-10): the hover card is unreachable on touch, so
    // the date and time must exist as text. Two tiny lines fit the grid's
    // 2.5rem icon-width Time track without changing the column template.
    return (
        <div className="inline-flex max-w-full flex-col items-center">
            <TooltipProvider delayDuration={150}>
                <Tooltip>
                    <TooltipTrigger asChild>
                        <button
                            type="button"
                            aria-label={`Call time ${label}`}
                            title={stamp.utc}
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
                            Duration {formatCallDuration(durationSeconds)} · {stamp.zone}
                        </div>
                        <div className="mt-1 text-muted-foreground">{stamp.utc}</div>
                    </TooltipContent>
                </Tooltip>
            </TooltipProvider>
            {/* aria-hidden: the button above already announces the full timestamp. */}
            <time
                dateTime={iso}
                title={stamp.utc}
                aria-hidden
                data-testid="call-timestamp-text"
                className="whitespace-nowrap text-center text-[9px] leading-[11px] tracking-tight text-muted-foreground tabular-nums"
            >
                {stamp.date}
                <br />
                {stamp.time}
                <br />
                {stamp.offset}
            </time>
        </div>
    );
}
