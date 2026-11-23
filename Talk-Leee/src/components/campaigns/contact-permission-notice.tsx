"use client";

/**
 * Amber notice on the campaign page: how many contacts have no permission to
 * call on record. Shows nothing when the server has no summary (older server)
 * or the count is zero.
 */
import { AlertTriangle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { summaryNotice, useContactPermissionSummary } from "@/lib/contact-permission-summary";

export function ContactPermissionNotice({
    campaignId,
    refreshKey,
    serverHasSummary,
    onShowLists,
}: {
    campaignId: string;
    /** False against an older server, which is then never asked. */
    serverHasSummary: boolean;
    /** Changes whenever the lists change, so the count is read again. */
    refreshKey: string;
    onShowLists: () => void;
}) {
    const notice = summaryNotice(useContactPermissionSummary(campaignId, refreshKey, serverHasSummary));
    if (!notice) return null;
    return (
        <div role="status" className="content-card flex items-start gap-3 border-amber-500/30 bg-amber-500/5">
            <AlertTriangle className="w-5 h-5 shrink-0 mt-0.5 text-amber-500" aria-hidden />
            <div className="flex-1 text-sm">
                <p className="font-medium text-amber-700 dark:text-amber-400">{notice.title}</p>
                <p className="text-muted-foreground mt-0.5">{notice.body}</p>
            </div>
            <Button size="sm" variant="outline" onClick={onShowLists}>Show contact lists</Button>
        </div>
    );
}
