"use client";

/**
 * Duplicate state shared by the campaign list's details modal and the
 * campaign detail page (production-readiness 3.1 / 3.2): the running flag and
 * the result message. Both surfaces copy through the same `duplicateCampaign`
 * in lib/campaign-duplicate, which asks the backend to make the copy. Nothing
 * is decided here: a refusal is the backend's own message, shown as the notice.
 */
import { useEffect, useRef, useState } from "react";

import {
    clearDuplicateNotice,
    duplicateCampaign,
    peekDuplicateNotice,
    type CampaignDuplicateNotice,
    type CampaignDuplicateResult,
} from "@/lib/campaign-duplicate";
import { cn } from "@/lib/utils";

export function duplicateResultNotice(result: CampaignDuplicateResult): CampaignDuplicateNotice {
    return { kind: "success", text: result.message };
}

export type CampaignDuplicateState = {
    /** Running, or no campaign. */
    disabled: boolean;
    running: boolean;
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
    const [running, setRunning] = useState(false);
    const [notice, setNotice] = useState<CampaignDuplicateNotice | null>(
        () => (campaignId ? peekDuplicateNotice(campaignId) : null),
    );
    const inFlight = useRef(false);
    const latestId = useRef(campaignId);

    // A different campaign (or a reopened modal) starts from nothing: no
    // message left over from another campaign.
    if (seenId !== campaignId) {
        setSeenId(campaignId);
        setNotice(campaignId ? peekDuplicateNotice(campaignId) : null);
    }

    useEffect(() => {
        latestId.current = campaignId;
        if (campaignId) clearDuplicateNotice(campaignId);
    }, [campaignId]);

    async function run() {
        if (!campaignId || inFlight.current) return;
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
            setNotice({ kind: "error", text: err instanceof Error ? err.message : String(err) });
        } finally {
            inFlight.current = false;
            setRunning(false);
        }
    }

    return {
        disabled: !campaignId || running,
        running,
        notice,
        run,
    };
}

const BOX_CLASS = "min-w-0 break-words rounded-md border px-2.5 py-1.5 text-sm font-medium";
const TONE_CLASS: Record<CampaignDuplicateNotice["kind"], string> = {
    success: "border-emerald-500/30 bg-emerald-500/10 text-emerald-800 dark:text-emerald-300",
    error: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-300",
};

/** The last result message, or nothing. */
export function CampaignDuplicateStatus({ duplicate, className }: { duplicate: CampaignDuplicateState; className?: string }) {
    if (!duplicate.notice) return null;
    return (
        <div className={cn("min-w-0 space-y-2", className)}>
            <p
                role={duplicate.notice.kind === "error" ? "alert" : "status"}
                data-testid="campaign-duplicate-notice"
                data-kind={duplicate.notice.kind}
                className={cn(BOX_CLASS, TONE_CLASS[duplicate.notice.kind])}
            >
                {duplicate.notice.text}
            </p>
        </div>
    );
}
