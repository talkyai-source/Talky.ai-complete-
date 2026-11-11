"use client";

/**
 * The inline result of a campaign Start / Resume, Pause or Stop
 * (production-readiness 3.2 + 3.3): one line per sentence, shown on the
 * campaign list and the campaign detail page in place of a native alert().
 */
import type { CampaignControlNotice } from "@/lib/campaign-control";
import { cn } from "@/lib/utils";

const TONE_CLASS: Record<CampaignControlNotice["kind"], string> = {
    success: "border-emerald-500/30 bg-emerald-500/10 text-emerald-800 dark:text-emerald-300",
    warning: "border-amber-500/30 bg-amber-500/10 text-amber-800 dark:text-amber-300",
    error: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-300",
};

export function CampaignControlNoticeBox({ notice, testId = "campaign-control-notice", className }: {
    notice: CampaignControlNotice | null | undefined;
    testId?: string;
    className?: string;
}) {
    if (!notice || notice.lines.length === 0) return null;
    return (
        <div
            role={notice.kind === "error" ? "alert" : "status"}
            data-testid={testId}
            data-kind={notice.kind}
            className={cn("min-w-0 space-y-1 break-words rounded-md border px-2.5 py-1.5 text-sm font-medium", TONE_CLASS[notice.kind], className)}
        >
            {notice.lines.map((line, index) => (
                <p key={`${index}-${line}`}>{line}</p>
            ))}
        </div>
    );
}
