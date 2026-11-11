"use client";

/**
 * Duplicate state shared by the campaign list's details modal and the
 * campaign detail page (production-readiness 3.1 / 3.2): the knowledge gate,
 * the running flag and the result message. Both surfaces copy through the
 * same `duplicateCampaign` in lib/campaign-duplicate.
 */
import { useEffect, useRef, useState } from "react";

import {
    CampaignDuplicateBlockedError,
    DUPLICATE_KNOWLEDGE_BLOCKED_TEXT,
    campaignDuplicateBlocked,
    clearDuplicateNotice,
    duplicateCampaign,
    peekDuplicateNotice,
    type CampaignDuplicateNotice,
    type CampaignDuplicateResult,
} from "@/lib/campaign-duplicate";
import { cn } from "@/lib/utils";

export function duplicateResultNotice(result: CampaignDuplicateResult): CampaignDuplicateNotice {
    return { kind: result.leadFieldsCopied ? "success" : "warning", text: result.message };
}

export type CampaignDuplicateState = {
    /** Checking knowledge, blocked by knowledge, running, or no campaign. */
    disabled: boolean;
    running: boolean;
    /** Why Duplicate is unavailable for this campaign; null when it is not blocked. */
    reason: string | null;
    notice: CampaignDuplicateNotice | null;
    run: () => Promise<void>;
};

/**
 * @param campaignId the campaign Duplicate would copy, or null when no
 *   Duplicate control is showing (closed modal, campaign not loaded yet).
 * @param onCreated runs once the copy exists (list: refetch; detail: open it).
 */
export function useCampaignDuplicate(
    campaignId: string | null,
    onCreated?: (result: CampaignDuplicateResult) => void | Promise<void>,
): CampaignDuplicateState {
    const [seenId, setSeenId] = useState(campaignId);
    // null = the knowledge read has not answered yet.
    const [blocked, setBlocked] = useState<boolean | null>(null);
    const [running, setRunning] = useState(false);
    const [notice, setNotice] = useState<CampaignDuplicateNotice | null>(
        () => (campaignId ? peekDuplicateNotice(campaignId) : null),
    );
    const inFlight = useRef(false);
    const latestId = useRef(campaignId);

    // A different campaign (or a reopened modal) starts from nothing: its own
    // knowledge answer, and no message left over from another campaign.
    if (seenId !== campaignId) {
        setSeenId(campaignId);
        setBlocked(null);
        setNotice(campaignId ? peekDuplicateNotice(campaignId) : null);
    }

    useEffect(() => {
        latestId.current = campaignId;
        if (!campaignId) return;
        clearDuplicateNotice(campaignId);
        let cancelled = false;
        void campaignDuplicateBlocked(campaignId).then((next) => {
            if (!cancelled) setBlocked(next);
        });
        return () => {
            cancelled = true;
        };
    }, [campaignId]);

    async function run() {
        if (!campaignId || blocked !== false || inFlight.current) return;
        const id = campaignId;
        inFlight.current = true;
        setRunning(true);
        setNotice(null);
        try {
            const result = await duplicateCampaign(id);
            try {
                await onCreated?.(result);
            } catch {
                /* the copy exists; its message below still applies */
            }
            if (latestId.current === id) setNotice(duplicateResultNotice(result));
        } catch (err) {
            if (latestId.current !== id) return;
            if (err instanceof CampaignDuplicateBlockedError) setBlocked(true);
            else setNotice({ kind: "error", text: err instanceof Error ? err.message : String(err) });
        } finally {
            inFlight.current = false;
            setRunning(false);
        }
    }

    return {
        disabled: !campaignId || blocked !== false || running,
        running,
        reason: blocked ? DUPLICATE_KNOWLEDGE_BLOCKED_TEXT : null,
        notice,
        run,
    };
}

const BOX_CLASS = "min-w-0 break-words rounded-md border px-2.5 py-1.5 text-sm font-medium";
const TONE_CLASS: Record<CampaignDuplicateNotice["kind"], string> = {
    success: "border-emerald-500/30 bg-emerald-500/10 text-emerald-800 dark:text-emerald-300",
    warning: "border-amber-500/30 bg-amber-500/10 text-amber-800 dark:text-amber-300",
    error: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-300",
};

/** The disabled reason and the last result message, or nothing. */
export function CampaignDuplicateStatus({ duplicate, className }: { duplicate: CampaignDuplicateState; className?: string }) {
    if (!duplicate.reason && !duplicate.notice) return null;
    return (
        <div className={cn("min-w-0 space-y-2", className)}>
            {duplicate.reason ? (
                <p role="status" data-testid="campaign-duplicate-reason" className={cn(BOX_CLASS, TONE_CLASS.warning)}>
                    {duplicate.reason}
                </p>
            ) : null}
            {duplicate.notice ? (
                <p
                    role={duplicate.notice.kind === "error" ? "alert" : "status"}
                    data-testid="campaign-duplicate-notice"
                    data-kind={duplicate.notice.kind}
                    className={cn(BOX_CLASS, TONE_CLASS[duplicate.notice.kind])}
                >
                    {duplicate.notice.text}
                </p>
            ) : null}
        </div>
    );
}
