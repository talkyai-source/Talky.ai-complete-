/**
 * Start / Resume, Pause and Stop for outbound campaigns (production-readiness
 * 3.2 + 3.3), shared by the campaign list and the campaign detail page:
 *
 *  - which campaigns can be started, and the button wording;
 *  - ONE set of campaign ids with a start (or stop / pause) in flight, read by
 *    every entry point, so a second request for the same campaign is ignored
 *    while the first is still running;
 *  - the exact result texts.
 *
 * Nothing here sends `first_speaker` on its own: `runCampaignStarts` takes it
 * from its caller, and the only caller is the shared Start dialog
 * (components/campaigns/campaign-start-dialog).
 */
import { useSyncExternalStore } from "react";

import {
    campaignStartErrorMessage,
    dashboardApi,
    type CampaignControlResponse,
    type CampaignStartOptions,
    type CampaignTerminationSummary,
    type MinutesStatus,
} from "@/lib/dashboard-api";

// ---------------------------------------------------------------- eligibility

/** A campaign can be started from exactly these backend statuses. */
export function canStartCampaign(status: string | null | undefined): boolean {
    const s = String(status ?? "").toLowerCase();
    return s === "draft" || s === "paused" || s === "stopped";
}

/** "Resume" for a paused campaign, "Start" for a draft or stopped one. */
export function campaignStartLabel(status: string | null | undefined): "Start" | "Resume" {
    return String(status ?? "").toLowerCase() === "paused" ? "Resume" : "Start";
}

function isRunning(status: string | null | undefined): boolean {
    return String(status ?? "").toLowerCase() === "running";
}

// ---------------------------------------------------------------------- texts

export function contactsQueuedText(jobsEnqueued: number): string {
    return `${jobsEnqueued} contacts queued for calling.`;
}

export function notStartedText(campaignName: string, reason: string): string {
    return `${campaignName}: not started — ${reason}`;
}

export function notPausedText(campaignName: string, reason = "not running"): string {
    return `${campaignName}: not paused — ${reason}`;
}

export function outOfMinutesText(minutes: MinutesStatus | null | undefined): string {
    return (
        `You're out of plan minutes (${minutes?.used_minutes}/${minutes?.allocated} used this month). ` +
        "Add minutes or upgrade your plan to start campaigns."
    );
}

export const LIVE_CALLS_ENDED_TEXT = "All live calls were ended.";
export const LIVE_CALLS_UNCHECKED_TEXT = "Live calls could not be checked. Some may still be ending.";
export function liveCallsEndingText(count: number): string {
    return count === 1 ? "1 live call is still ending." : `${count} live calls are still ending.`;
}

type TerminationStatus = CampaignTerminationSummary["status"];

// Anything other than confirmed / partial is read as lookup_failed, the same
// default the backend applies (campaigns.py:1181, :1274).
function terminationStatus(summary: Partial<CampaignTerminationSummary> | null | undefined): TerminationStatus {
    return summary?.status === "confirmed" || summary?.status === "partial" ? summary.status : "lookup_failed";
}

// The calls still ending: `deferred`, else `unconfirmed` (campaigns.py:1178, :1271).
function stillEnding(summary: Partial<CampaignTerminationSummary> | null | undefined): number {
    return Number(summary?.deferred ?? summary?.unconfirmed ?? 0) || 0;
}

/** The one sentence for a Stop / Pause result. Never carries the campaign id. */
export function terminationNoticeText(summary: Partial<CampaignTerminationSummary> | null | undefined): string {
    const status = terminationStatus(summary);
    if (status === "confirmed") return LIVE_CALLS_ENDED_TEXT;
    if (status === "partial") return liveCallsEndingText(stillEnding(summary));
    return LIVE_CALLS_UNCHECKED_TEXT;
}

export type CampaignControlNotice = { kind: "success" | "warning" | "error"; lines: string[] };

function terminationNotice(summary: Partial<CampaignTerminationSummary> | null | undefined): CampaignControlNotice {
    return {
        kind: terminationStatus(summary) === "confirmed" ? "success" : "warning",
        lines: [terminationNoticeText(summary)],
    };
}

// ------------------------------------------------------------ in-flight store

export type CampaignActionKind = "starting" | "stopping" | "pausing";
export type CampaignsInFlight = Readonly<Record<CampaignActionKind, ReadonlySet<string>>>;

let inFlight: CampaignsInFlight = { starting: new Set(), stopping: new Set(), pausing: new Set() };
const listeners = new Set<() => void>();

function publish(kind: CampaignActionKind, next: Set<string>) {
    inFlight = { ...inFlight, [kind]: next };
    listeners.forEach((listener) => listener());
}

/** Marks the action as running. False when it already is: the caller must not send a second request. */
export function beginCampaignAction(kind: CampaignActionKind, campaignId: string): boolean {
    if (inFlight[kind].has(campaignId)) return false;
    publish(kind, new Set(inFlight[kind]).add(campaignId));
    return true;
}

export function endCampaignAction(kind: CampaignActionKind, campaignId: string): void {
    if (!inFlight[kind].has(campaignId)) return;
    const next = new Set(inFlight[kind]);
    next.delete(campaignId);
    publish(kind, next);
}

export function campaignsInFlight(): CampaignsInFlight {
    return inFlight;
}

function subscribe(listener: () => void) {
    listeners.add(listener);
    return () => {
        listeners.delete(listener);
    };
}

/** The shared in-flight sets, re-rendering the caller whenever one changes. */
export function useCampaignsInFlight(): CampaignsInFlight {
    return useSyncExternalStore(subscribe, campaignsInFlight, campaignsInFlight);
}

// ---------------------------------------------------------------------- start

export type CampaignStartOutcome = {
    /** Sum of the responses' `jobs_enqueued`; null when no campaign started. */
    queued: number | null;
    startedIds: string[];
    notStarted: Array<{ id: string; name: string; reason: string }>;
};

/**
 * Starts each campaign in turn. A campaign whose start is already in flight is
 * ignored. `afterResponses` runs once every response is in and while the
 * started campaigns still read as "Preparing", so a caller can reload the
 * backend status before the label changes.
 */
export async function runCampaignStarts(
    targets: Array<{ id: string; name: string }>,
    options: CampaignStartOptions,
    afterResponses?: (outcome: CampaignStartOutcome) => void | Promise<void>,
): Promise<CampaignStartOutcome> {
    const begun = targets.filter((target) => beginCampaignAction("starting", target.id));
    const outcome: CampaignStartOutcome = { queued: null, startedIds: [], notStarted: [] };
    try {
        for (const target of begun) {
            try {
                const response = await dashboardApi.startCampaign(target.id, options);
                outcome.queued = (outcome.queued ?? 0) + (Number(response?.jobs_enqueued) || 0);
                outcome.startedIds.push(target.id);
            } catch (err) {
                outcome.notStarted.push({ id: target.id, name: target.name, reason: campaignStartErrorMessage(err) });
                endCampaignAction("starting", target.id);
            }
        }
        try {
            await afterResponses?.(outcome);
        } catch {
            /* the starts themselves succeeded; a failed refresh is the caller's to show */
        }
    } finally {
        begun.forEach((target) => endCampaignAction("starting", target.id));
    }
    return outcome;
}

/** The message for a finished start: the queued count, then every campaign that did not start. */
export function startOutcomeNotice(outcome: CampaignStartOutcome): CampaignControlNotice | null {
    const lines = [
        ...(outcome.queued === null ? [] : [contactsQueuedText(outcome.queued)]),
        ...outcome.notStarted.map((entry) => notStartedText(entry.name, entry.reason)),
    ];
    if (lines.length === 0) return null;
    return { kind: outcome.notStarted.length === 0 ? "success" : outcome.queued === null ? "error" : "warning", lines };
}

// --------------------------------------------------------------- stop / pause

async function runControl(
    kind: "stopping" | "pausing",
    campaignId: string,
    send: (id: string) => Promise<CampaignControlResponse>,
    fallback: string,
    afterResponse?: () => void | Promise<void>,
): Promise<CampaignControlNotice | null> {
    if (!beginCampaignAction(kind, campaignId)) return null;
    try {
        let response: CampaignControlResponse;
        try {
            response = await send(campaignId);
        } catch (err) {
            return { kind: "error", lines: [err instanceof Error && err.message ? err.message : fallback] };
        }
        try {
            await afterResponse?.();
        } catch {
            /* the request itself succeeded */
        }
        return terminationNotice(response?.termination_summary);
    } finally {
        endCampaignAction(kind, campaignId);
    }
}

/**
 * Stops one campaign and returns the notice to show; null when a stop for it is
 * already running. `afterResponse` (a reload) runs while it still reads "Stopping".
 */
export function runCampaignStop(campaignId: string, afterResponse?: () => void | Promise<void>) {
    return runControl("stopping", campaignId, (id) => dashboardApi.stopCampaign(id), "Failed to stop campaign", afterResponse);
}

/** Pauses one campaign; same contract as `runCampaignStop`. */
export function runCampaignPause(campaignId: string, afterResponse?: () => void | Promise<void>) {
    return runControl("pausing", campaignId, (id) => dashboardApi.pauseCampaign(id), "Failed to pause campaign", afterResponse);
}

/**
 * Pauses the running campaigns among `targets`. Every other campaign is skipped
 * and listed. Returns null when there is nothing to report.
 */
export async function runCampaignPauses(
    targets: Array<{ id: string; name: string; status: string }>,
    afterResponses?: () => void | Promise<void>,
): Promise<CampaignControlNotice | null> {
    const summaries: Array<Partial<CampaignTerminationSummary> | null | undefined> = [];
    const notPaused: string[] = [];
    const begun: string[] = [];
    let refused = false;
    try {
        for (const target of targets) {
            if (!isRunning(target.status)) {
                notPaused.push(notPausedText(target.name));
                continue;
            }
            if (!beginCampaignAction("pausing", target.id)) continue;
            begun.push(target.id);
            try {
                const response = await dashboardApi.pauseCampaign(target.id);
                summaries.push(response?.termination_summary);
            } catch (err) {
                refused = true;
                notPaused.push(notPausedText(target.name, err instanceof Error && err.message ? err.message : "Failed to pause campaign"));
            }
        }
        if (summaries.length > 0) {
            try {
                await afterResponses?.();
            } catch {
                /* the pauses themselves succeeded */
            }
        }
    } finally {
        begun.forEach((id) => endCampaignAction("pausing", id));
    }

    const statuses = summaries.map(terminationStatus);
    const lines: string[] = [];
    if (statuses.includes("partial")) {
        lines.push(liveCallsEndingText(summaries.reduce<number>(
            (total, summary) => total + (terminationStatus(summary) === "partial" ? stillEnding(summary) : 0), 0,
        )));
    }
    if (statuses.includes("lookup_failed")) lines.push(LIVE_CALLS_UNCHECKED_TEXT);
    if (summaries.length > 0 && lines.length === 0) lines.push(LIVE_CALLS_ENDED_TEXT);
    const cleanPause = summaries.length > 0 && statuses.every((status) => status === "confirmed");
    lines.push(...notPaused);
    if (lines.length === 0) return null;
    return {
        kind: summaries.length === 0 && refused ? "error" : cleanPause && notPaused.length === 0 ? "success" : "warning",
        lines,
    };
}
