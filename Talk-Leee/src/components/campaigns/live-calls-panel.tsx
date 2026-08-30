"use client";

/**
 * Live calls panel — the "wallboard" view of what's happening right now.
 *
 * Polls GET /api/v1/calls/live every 2 seconds and renders a table of
 * in-flight calls (queued → dialing → ringing → answered → in_call → ended).
 * Calls that have ended stay visible for 60 s so the operator sees the
 * outcome before the row vanishes.
 *
 * Deliberately uses polling instead of SSE/WebSocket for v1:
 *   - one less moving piece on infra (no Redis pubsub),
 *   - works through any load balancer / CDN / cookie boundary,
 *   - 2 s feels live enough for operator-facing dashboards.
 * If we later need <500 ms updates (whisper mode, listen-in), switch to
 * an SSE subscriber on top of the same backend events table.
 */

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Phone, PhoneCall, PhoneOff, PhoneIncoming, CircleCheck, CircleX, Loader2, ChevronRight, ChevronDown, FileText } from "lucide-react";

import { api } from "@/lib/api";
import type { CallTerminationStatus, LiveCallItem } from "@/lib/api";
import { dashboardApi } from "@/lib/dashboard-api";
import { extendedApi } from "@/lib/extended-api";
import { isApiClientError } from "@/lib/http-client";
import { getRecordingCapabilities } from "@/lib/media-permissions";
import { useEffectivePermissions } from "@/lib/queries/inbound-queries";

// Per-call recording + transcript, fetched the moment a call ends so the
// operator can review it inline without stopping the campaign or leaving the
// page. Recording/transcript finalise a beat AFTER the call flips to "ended",
// so `loadDetail` polls a few times until they're ready.
type CallReview = {
    loading: boolean;
    ready: boolean;
    transcript?: string;
    recordingId?: string | null;
    error?: string;
    blobUrl?: string;
    recordingLoading?: boolean;
    recordingError?: string;
};

const POLL_INTERVAL_MS = 1500;
const RECENT_WINDOW_SECONDS = 60;

type LiveCall = LiveCallItem;

export type LocalTermination = {
    status: Exclude<CallTerminationStatus, "none">;
    error: string | null;
    submitting: boolean;
};

type TerminationView =
    | { phase: "none"; error: null; message: null }
    | { phase: "pending"; error: string | null; message: string }
    | { phase: "failed"; error: string; message: string };

const TERMINAL_CALL_STATUSES = new Set([
    "ended", "completed", "failed", "cancelled", "canceled", "busy",
    "no_answer", "rejected",
]);

export function isTerminalLiveCall(status: string): boolean {
    return TERMINAL_CALL_STATUSES.has(status.trim().toLowerCase());
}

export function terminationView(
    call: Pick<LiveCall, "status" | "termination_status" | "termination_error">,
    local?: LocalTermination,
): TerminationView {
    if (isTerminalLiveCall(call.status)) {
        return { phase: "none", error: null, message: null };
    }

    if (local?.submitting) {
        return { phase: "pending", error: null, message: "Sending hangup request…" };
    }

    const serverStatus = call.termination_status && call.termination_status !== "none"
        ? call.termination_status
        : call.status.trim().toLowerCase() === "termination_pending"
            ? "requested"
            : undefined;
    const status = local?.status ?? serverStatus;
    const error = local ? local.error : call.termination_error ?? null;

    if (status === "failed") {
        return {
            phase: "failed",
            error: error ?? "The provider did not confirm that the call ended.",
            message: "Hangup failed",
        };
    }
    if (status === "confirmed") {
        return {
            phase: "pending",
            error,
            message: "Provider confirmed; syncing call state…",
        };
    }
    if (status === "requested") {
        return {
            phase: "pending",
            error,
            message: "Awaiting provider confirmation…",
        };
    }
    return { phase: "none", error: null, message: null };
}

function hangupErrorMessage(error: unknown): string {
    if (isApiClientError(error)) {
        const details = error.details && typeof error.details === "object"
            ? error.details as Record<string, unknown>
            : undefined;
        const providerError = typeof details?.provider_hangup_error === "string"
            ? details.provider_hangup_error.trim()
            : "";
        if (providerError) return providerError;

        const reason = typeof details?.reason === "string" ? details.reason.trim() : "";
        if (reason === "confirmation_timeout" || reason === "hangup_unconfirmed") {
            return "The provider did not confirm that the call ended before the timeout.";
        }
        if (error.code === "termination_unconfirmed") {
            return "The provider did not confirm that the call ended.";
        }
    }
    return error instanceof Error ? error.message : "Failed to hang up call";
}

type StatusLook = {
    label: string;
    pillClass: string;
    Icon: typeof PhoneCall;
    iconClass: string;
    pulse?: boolean;
};

function statusLook(status: string, outcome?: string | null): StatusLook {
    // `outcome` only matters once status === ended.
    switch (status) {
        case "queued":
            return {
                label: "Queued",
                pillClass: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
                Icon: Loader2,
                iconClass: "animate-spin opacity-70",
            };
        case "dialing":
        case "initiated":
            return {
                label: "Dialing",
                pillClass: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300",
                Icon: PhoneCall,
                iconClass: "",
            };
        case "ringing":
            return {
                label: "Ringing",
                pillClass: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
                Icon: PhoneIncoming,
                iconClass: "animate-pulse",
                pulse: true,
            };
        case "answered":
        case "in_call":
            return {
                label: status === "in_call" ? "In call" : "Answered",
                pillClass: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
                Icon: PhoneCall,
                iconClass: "",
            };
        case "ended":
        case "completed":
        case "failed": {
            const isPositive = outcome === "answered" || outcome === "customer_hung_up" || outcome === "agent_hung_up";
            const isHard = outcome === "rejected" || outcome === "unreachable" || outcome === "network_failure" || outcome === "failed";
            return {
                label: outcome ? humanOutcome(outcome) : "Ended",
                pillClass: isPositive
                    ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300"
                    : isHard
                      ? "bg-red-50 text-red-700 dark:bg-red-950/60 dark:text-red-300"
                      : "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
                Icon: isPositive ? CircleCheck : isHard ? CircleX : PhoneOff,
                iconClass: "",
            };
        }
        default:
            return {
                label: status,
                pillClass: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
                Icon: Phone,
                iconClass: "",
            };
    }
}

function terminationStatusLook(view: TerminationView): StatusLook | null {
    if (view.phase === "pending") {
        return {
            label: "Ending",
            pillClass: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
            Icon: Loader2,
            iconClass: "animate-spin",
        };
    }
    if (view.phase === "failed") {
        return {
            label: "Hangup failed",
            pillClass: "bg-red-50 text-red-700 dark:bg-red-950/60 dark:text-red-300",
            Icon: CircleX,
            iconClass: "",
        };
    }
    return null;
}

function humanOutcome(outcome: string): string {
    switch (outcome) {
        case "answered": return "Answered";
        case "busy": return "Busy";
        case "no_answer": return "No answer";
        case "voicemail": return "Voicemail";
        case "rejected": return "Rejected";
        case "unreachable": return "Unreachable";
        case "network_failure": return "Network failure";
        case "cancelled": return "Cancelled";
        case "customer_hung_up": return "Customer hung up";
        case "agent_hung_up": return "Agent ended";
        case "failed": return "Failed";
        default: return outcome.replace(/_/g, " ");
    }
}

function fmtDuration(secs: number | null | undefined): string {
    if (secs === null || secs === undefined) return "—";
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    return `${m}:${String(s).padStart(2, "0")}`;
}

function elapsedSeconds(startIso: string | null | undefined, nowMs: number): number | null {
    if (!startIso) return null;
    const start = Date.parse(startIso);
    if (Number.isNaN(start)) return null;
    return Math.max(0, Math.floor((nowMs - start) / 1000));
}

export type LiveCallsPanelProps = {
    /** When set, scopes the panel to one campaign. Omit for tenant-wide view. */
    campaignId?: string;
    /** Optional title override. */
    title?: string;
};

export function LiveCallsPanel({ campaignId, title = "Live calls" }: LiveCallsPanelProps) {
    const permissions = useEffectivePermissions();
    const recordingCapabilities = getRecordingCapabilities(
        permissions.isSuccess ? permissions.data.permissions : undefined,
    );
    const canPlayMedia = permissions.isSuccess && recordingCapabilities.canRead;
    const canDownloadMedia = permissions.isSuccess && recordingCapabilities.canDownload;
    const canPlayMediaRef = useRef(canPlayMedia);
    useEffect(() => {
        canPlayMediaRef.current = canPlayMedia;
    }, [canPlayMedia]);
    const [items, setItems] = useState<LiveCall[]>([]);
    const [error, setError] = useState<string | null>(null);
    // Tick every second so elapsed-time counters update between polls.
    const [nowMs, setNowMs] = useState<number>(() => Date.now());
    const [terminations, setTerminations] = useState<Record<string, LocalTermination>>({});
    const hangupRequestsInFlight = useRef<Set<string>>(new Set());
    const aborted = useRef(false);

    // Inline recording + transcript review for ended calls.
    const [expanded, setExpanded] = useState<Record<string, boolean>>({});
    const [reviews, setReviews] = useState<Record<string, CallReview>>({});
    const reviewsRef = useRef<Record<string, CallReview>>({});
    // Mirror the committed reviews into a ref from an effect, never during
    // render: under the React Compiler a render-phase ref write is not
    // guaranteed to run (the render can be memoized away), which would leave
    // the unmount cleanup below revoking a stale set of object URLs.
    useEffect(() => {
        reviewsRef.current = reviews;
    }, [reviews]);
    const requested = useRef<Set<string>>(new Set());

    // Release any recording object URLs on unmount so they don't leak browser
    // memory over a long operator session (playback mints a blob URL
    // per played recording).
    useEffect(() => {
        return () => {
            for (const rv of Object.values(reviewsRef.current)) {
                if (rv.blobUrl) URL.revokeObjectURL(rv.blobUrl);
            }
        };
    }, []);

    useEffect(() => {
        if (canPlayMedia) return;
        setReviews((current) => {
            let changed = false;
            const next: Record<string, CallReview> = {};
            for (const [callId, review] of Object.entries(current)) {
                if (review.blobUrl) {
                    URL.revokeObjectURL(review.blobUrl);
                }
                if (review.blobUrl || review.recordingLoading) changed = true;
                next[callId] = review.blobUrl || review.recordingLoading
                    ? { ...review, blobUrl: undefined, recordingLoading: false, recordingError: undefined }
                    : review;
            }
            return changed ? next : current;
        });
    }, [canPlayMedia]);

    // Fetch a call's transcript + recording id. Polls a few times because the
    // recording upload + transcript persist land a beat after "ended".
    const loadDetail = useCallback(async (callId: string, attempt = 0) => {
        try {
            const call = await dashboardApi.getCall(callId);
            const hasTranscript = !!(call.transcript && call.transcript.trim());
            const hasRecording = !!call.recording_id;
            const ready = hasTranscript || hasRecording;
            setReviews((r) => ({
                ...r,
                [callId]: {
                    loading: !ready && attempt < 6,
                    ready,
                    transcript: call.transcript,
                    recordingId: call.recording_id ?? null,
                },
            }));
            if (!ready && attempt < 6 && !aborted.current) {
                window.setTimeout(() => void loadDetail(callId, attempt + 1), 1500);
            }
        } catch (e) {
            setReviews((r) => ({
                ...r,
                [callId]: {
                    ...(r[callId] ?? { ready: false }),
                    loading: false,
                    error: e instanceof Error ? e.message : "Failed to load",
                },
            }));
        }
    }, []);

    // Lazily fetch the authenticated recording bytes only after an explicit
    // "Load recording" click. Expanding a row never downloads audio.
    //
    // SECURITY NOTE — `recordings:download` is NOT enforceable in this
    // component. Playback needs the audio bytes in the page, and every route
    // that can deliver them is authorized by `recordings:read`:
    //   - GET /recordings/{id}/stream  → RECORDINGS_READ (bytes, or a 302 to a
    //     presigned S3 URL). It lives on the API origin and is authorized by
    //     the bearer/HttpOnly-cookie pair the fetch layer attaches, so it can
    //     NOT be used as a bare `<audio src>`; and even if it could, that URL
    //     would itself be copyable.
    //   - GET /recordings/{id}/url    → RECORDINGS_DOWNLOAD (correctly gated,
    //     so it is unavailable to exactly the users we want to restrict).
    // There is no read-scoped signed playback URL to point `<audio src>` at,
    // so we keep blob playback and treat `controlsList="nodownload"` as what it
    // is — a UI hint. A download-denied operator can still lift the bytes out
    // of devtools. Real enforcement has to happen server-side (e.g. a
    // read-scoped, short-lived, single-use streaming token, or watermarked /
    // transcoded preview audio). Until then we minimise exposure: the object
    // URL is minted only on an explicit click, and for download-denied users it
    // is revoked as soon as playback stops (see `releaseRecording`) and on
    // unmount, so no long-lived copyable handle is left on the page.
    const loadRecording = useCallback(async (callId: string, recordingId: string) => {
        if (!canPlayMediaRef.current) return;
        setReviews((r) => ({ ...r, [callId]: { ...(r[callId] ?? { ready: true }), recordingLoading: true, recordingError: undefined } }));
        try {
            const blob = await extendedApi.fetchRecordingPlaybackBlob(recordingId);
            const url = URL.createObjectURL(blob);
            if (aborted.current || !canPlayMediaRef.current) {
                URL.revokeObjectURL(url);
                return;
            }
            setReviews((r) => ({ ...r, [callId]: { ...(r[callId] ?? { ready: true }), blobUrl: url, recordingLoading: false } }));
        } catch (error) {
            if (!aborted.current) {
                setReviews((r) => ({
                    ...r,
                    [callId]: {
                        ...(r[callId] ?? { ready: true }),
                        recordingLoading: false,
                        recordingError: error instanceof Error ? error.message : "Failed to load recording",
                    },
                }));
            }
        }
    }, []);

    // Revoke a recording's object URL and drop it from state, so the row falls
    // back to the "Load recording" button. Called when playback stops for a
    // user without `recordings:download`: the handle only exists while the
    // audio is actually playing.
    const releaseRecording = useCallback((callId: string) => {
        const url = reviewsRef.current[callId]?.blobUrl;
        if (!url) return;
        URL.revokeObjectURL(url);
        setReviews((r) => {
            const review = r[callId];
            if (!review?.blobUrl) return r;
            return { ...r, [callId]: { ...review, blobUrl: undefined } };
        });
    }, []);

    const toggleExpand = useCallback((callId: string) => {
        setExpanded((e) => ({ ...e, [callId]: !e[callId] }));
    }, []);

    async function handleHangup(callId: string) {
        const call = items.find((item) => item.id === callId);
        if (
            !call
            || hangupRequestsInFlight.current.has(callId)
            || terminationView(call, terminations[callId]).phase === "pending"
        ) return;

        hangupRequestsInFlight.current.add(callId);
        setTerminations((current) => ({
            ...current,
            [callId]: { status: "requested", error: null, submitting: true },
        }));
        try {
            const result = await api.hangupCall(callId);
            const failed = result.status === "failed" || result.termination_status === "failed";
            const responseStatus: LocalTermination["status"] = failed
                ? "failed"
                : result.status === "confirmed"
                    || result.status === "already_terminal"
                    || result.termination_status === "confirmed"
                    ? "confirmed"
                    : "requested";
            const unconfirmedError = result.provider_hangup_error
                ?? (result.status === "confirmed" && !result.provider_hangup_confirmed
                    ? "The provider has not confirmed the hangup yet."
                    : result.status === "requested" && !result.provider_hangup_requested
                        ? "The provider has not acknowledged the hangup request yet."
                        : null);
            setTerminations((current) => ({
                ...current,
                [callId]: {
                    status: responseStatus,
                    error: failed
                        ? unconfirmedError ?? "The provider did not confirm that the call ended."
                        : unconfirmedError,
                    submitting: false,
                },
            }));
        } catch (err) {
            setTerminations((current) => ({
                ...current,
                [callId]: {
                    status: "failed",
                    error: hangupErrorMessage(err),
                    submitting: false,
                },
            }));
        } finally {
            hangupRequestsInFlight.current.delete(callId);
        }
    }

    useEffect(() => {
        aborted.current = false;
        let cancelTimer: number | undefined;

        const poll = async () => {
            try {
                const res = await api.listLiveCalls({
                    campaignId,
                    recentWindowSeconds: RECENT_WINDOW_SECONDS,
                });
                if (aborted.current) return;
                setItems(res.items);
                setTerminations((current) => {
                    const polledById = new Map(res.items.map((item) => [item.id, item]));
                    let changed = false;
                    const next: Record<string, LocalTermination> = {};
                    for (const [callId, local] of Object.entries(current)) {
                        const polled = polledById.get(callId);
                        if (!polled || isTerminalLiveCall(polled.status)) {
                            changed = true;
                            continue;
                        }
                        const serverStatus = polled.termination_status;
                        if (serverStatus && serverStatus !== "none" && !local.submitting) {
                            const reconciled: LocalTermination = {
                                status: serverStatus,
                                error: polled.termination_error ?? null,
                                submitting: false,
                            };
                            next[callId] = reconciled;
                            if (reconciled.status !== local.status || reconciled.error !== local.error) changed = true;
                        } else {
                            next[callId] = local;
                        }
                    }
                    return changed ? next : current;
                });
                setError(null);
            } catch (err) {
                if (aborted.current) return;
                // Don't blank the panel on a transient error — just surface it.
                setError(err instanceof Error ? err.message : "Failed to load live calls");
            } finally {
                if (!aborted.current) {
                    cancelTimer = window.setTimeout(poll, POLL_INTERVAL_MS);
                }
            }
        };

        void poll();
        const tick = window.setInterval(() => setNowMs(Date.now()), 1000);

        return () => {
            aborted.current = true;
            if (cancelTimer !== undefined) window.clearTimeout(cancelTimer);
            window.clearInterval(tick);
        };
    }, [campaignId]);

    const live = useMemo(
        () => items.filter((it) => !isTerminalLiveCall(it.status)),
        [items],
    );
    const recentlyEnded = useMemo(
        () => items.filter((it) => isTerminalLiveCall(it.status)),
        [items],
    );

    // Prefetch each call's transcript + recording id the instant it ends, so
    // expanding the row shows everything immediately rather than after a fetch.
    // Once per call (guarded by `requested`).
    useEffect(() => {
        for (const c of recentlyEnded) {
            if (!requested.current.has(c.id)) {
                requested.current.add(c.id);
                void loadDetail(c.id);
            }
        }
    }, [recentlyEnded, loadDetail]);

    return (
        <div className="rounded-2xl border border-gray-200 dark:border-white/10 bg-white dark:bg-white/5 shadow-sm overflow-hidden">
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-white/10">
                <div className="flex items-center gap-2">
                    <span className="relative flex h-2.5 w-2.5">
                        <span className={`animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-70 ${live.length === 0 ? "hidden" : ""}`} />
                        <span className={`relative inline-flex rounded-full h-2.5 w-2.5 ${live.length > 0 ? "bg-emerald-500" : "bg-zinc-400"}`} />
                    </span>
                    <h3 className="text-sm font-semibold text-gray-900 dark:text-zinc-100">{title}</h3>
                    <span className="text-xs text-muted-foreground">
                        {live.length} in flight
                        {recentlyEnded.length > 0 ? ` · ${recentlyEnded.length} just ended` : ""}
                    </span>
                </div>
                {error && (
                    <span className="text-xs text-red-600 dark:text-red-400 truncate max-w-[40%]" title={error}>
                        {error}
                    </span>
                )}
            </div>

            {items.length === 0 ? (
                <div className="px-4 py-6 text-sm text-muted-foreground">
                    No calls in flight. Start the campaign to see live status here.
                </div>
            ) : (
                <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                        <thead className="text-xs uppercase text-muted-foreground bg-gray-50 dark:bg-white/5">
                            <tr>
                                <th className="px-4 py-2 text-left font-medium">Direction</th>
                                <th className="px-4 py-2 text-left font-medium">To</th>
                                <th className="px-4 py-2 text-left font-medium">From</th>
                                <th className="px-4 py-2 text-left font-medium">Status</th>
                                <th className="px-4 py-2 text-left font-medium">Duration</th>
                                <th className="px-4 py-2 text-left font-medium">Started</th>
                                <th className="px-4 py-2 text-right font-medium">Action</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-200 dark:divide-white/10">
                            {items.map((c) => {
                                const terminal = isTerminalLiveCall(c.status);
                                const termination = terminationView(c, terminations[c.id]);
                                const look = terminationStatusLook(termination) ?? statusLook(c.status, c.outcome);
                                const live = !terminal;
                                const elapsed = live
                                    ? elapsedSeconds(c.answered_at ?? c.started_at, nowMs)
                                    : c.duration_seconds ?? null;
                                const rv = reviews[c.id];
                                const isOpen = !!expanded[c.id];
                                const inbound = c.direction === "inbound";
                                const displayTo = inbound ? c.called_did ?? c.to_number : c.to_number;
                                const displayFrom = inbound
                                    ? c.caller_ani ?? "Private"
                                    : c.caller_id ?? "—";
                                return (
                                    <Fragment key={c.id}>
                                        <tr
                                            className={`hover:bg-gray-50 dark:hover:bg-white/[0.04] ${!live ? "cursor-pointer" : ""}`}
                                            onClick={!live ? () => toggleExpand(c.id) : undefined}
                                        >
                                            <td className="px-4 py-2">
                                                <span className={`inline-flex rounded-full px-2 py-0.5 text-[11px] font-medium ${inbound
                                                    ? "bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-300"
                                                    : "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300"}`}>
                                                    {inbound ? "Inbound" : "Outbound"}
                                                </span>
                                            </td>
                                            <td className="px-4 py-2 font-mono text-sm text-gray-900 dark:text-zinc-100">
                                                <span className="inline-flex items-center gap-1.5">
                                                    {!live && (
                                                        isOpen
                                                            ? <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
                                                            : <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
                                                    )}
                                                    {displayTo}
                                                </span>
                                            </td>
                                            <td className="px-4 py-2 font-mono text-xs text-muted-foreground">
                                                {displayFrom}
                                            </td>
                                            <td className="px-4 py-2">
                                                <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${look.pillClass}`}>
                                                    <look.Icon className={`h-3.5 w-3.5 ${look.iconClass}`} aria-hidden />
                                                    {look.label}
                                                </span>
                                                {termination.phase !== "none" ? (
                                                    <div
                                                        className={`mt-1 max-w-56 text-[11px] leading-tight ${termination.error ? "text-red-600 dark:text-red-400" : "text-muted-foreground"}`}
                                                        role={termination.error ? "alert" : "status"}
                                                        title={termination.error ?? termination.message}
                                                    >
                                                        {termination.error ?? termination.message}
                                                    </div>
                                                ) : null}
                                                {inbound && (c.admission_status || c.consent_status) ? (
                                                    <div className="mt-1 text-[11px] leading-tight text-muted-foreground">
                                                        {c.admission_status
                                                            ? `Admission: ${c.admission_status.replace(/_/g, " ")}`
                                                            : null}
                                                        {c.admission_status && c.consent_status ? " · " : null}
                                                        {c.consent_status
                                                            ? `Consent: ${c.consent_status.replace(/_/g, " ")}`
                                                            : null}
                                                    </div>
                                                ) : null}
                                            </td>
                                            <td className="px-4 py-2 font-mono text-sm tabular-nums">
                                                {fmtDuration(elapsed)}
                                            </td>
                                            <td className="px-4 py-2 text-xs text-muted-foreground">
                                                {c.started_at ? new Date(c.started_at).toLocaleTimeString() : "—"}
                                            </td>
                                            <td className="px-4 py-2 text-right">
                                                {live ? (
                                                    <button
                                                        type="button"
                                                        onClick={(e) => { e.stopPropagation(); handleHangup(c.id); }}
                                                        disabled={termination.phase === "pending"}
                                                        aria-label={termination.phase === "failed"
                                                            ? `Retry hangup for call to ${displayTo}`
                                                            : termination.phase === "pending"
                                                                ? `Ending call to ${displayTo}`
                                                                : `Hang up call to ${displayTo}`}
                                                        title={termination.phase === "failed" ? "Retry hangup" : termination.message ?? "Hang up"}
                                                        className={`inline-flex h-7 items-center justify-center gap-1 rounded-md text-red-600 transition-colors hover:bg-red-100 dark:text-red-400 dark:hover:bg-red-950/50 disabled:cursor-wait disabled:opacity-60 ${termination.phase === "failed" ? "px-2 text-xs font-medium" : "w-7"}`}
                                                    >
                                                        {termination.phase === "pending" ? (
                                                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                                        ) : (
                                                            <PhoneOff className="h-3.5 w-3.5" />
                                                        )}
                                                        {termination.phase === "failed" ? "Retry" : null}
                                                    </button>
                                                ) : (
                                                    <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                                                        <FileText className="h-3.5 w-3.5" aria-hidden />
                                                        {rv?.loading && !rv?.ready ? "Finalizing…" : "Recording & transcript"}
                                                    </span>
                                                )}
                                            </td>
                                        </tr>
                                        {!live && isOpen && (
                                            <tr className="bg-gray-50/70 dark:bg-white/[0.02]">
                                                <td colSpan={7} className="px-4 py-3">
                                                    <CallReviewPanel
                                                        review={rv}
                                                        onLoadRecording={() => {
                                                            if (rv?.recordingId) void loadRecording(c.id, rv.recordingId);
                                                        }}
                                                        onReleaseRecording={() => releaseRecording(c.id)}
                                                        canPlayMedia={canPlayMedia}
                                                        canDownloadMedia={canDownloadMedia}
                                                    />
                                                </td>
                                            </tr>
                                        )}
                                    </Fragment>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}

/** Inline recording player + transcript for a just-ended call. */
function CallReviewPanel({
    review,
    onLoadRecording,
    onReleaseRecording,
    canPlayMedia,
    canDownloadMedia,
}: {
    review?: CallReview;
    onLoadRecording: () => void;
    onReleaseRecording: () => void;
    canPlayMedia: boolean;
    canDownloadMedia: boolean;
}) {
    const stillFinalizing =
        !review ||
        (review.loading && !review.ready && !review.transcript && !review.recordingId);

    if (stillFinalizing) {
        return (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                Finalizing recording &amp; transcript…
            </div>
        );
    }
    if (review.error) {
        return (
            <div className="text-xs text-red-600 dark:text-red-400">
                Couldn&apos;t load this call: {review.error}
            </div>
        );
    }

    const hasTranscript = !!(review.transcript && review.transcript.trim());
    return (
        <div className="space-y-3">
            <div>
                {review.blobUrl ? (
                    // `controlsList="nodownload"` is a browser HINT, not a
                    // control — it hides the menu item and nothing more. For a
                    // user without `recordings:download` we additionally revoke
                    // the object URL the moment playback stops, so the page is
                    // not left holding a freely copyable handle to the audio.
                    // See the SECURITY NOTE on `loadRecording`: withholding the
                    // bytes entirely is only possible server-side.
                    <audio
                        controls
                        controlsList={canDownloadMedia ? undefined : "nodownload"}
                        src={review.blobUrl}
                        onPause={canDownloadMedia ? undefined : onReleaseRecording}
                        onEnded={canDownloadMedia ? undefined : onReleaseRecording}
                        className="h-9 w-full max-w-md"
                    />
                ) : review.recordingId && canPlayMedia ? (
                    review.recordingLoading ? (
                        <div className="flex items-center gap-2 text-xs text-muted-foreground">
                            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                            Loading recording…
                        </div>
                    ) : (
                        <button
                            type="button"
                            onClick={onLoadRecording}
                            className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-xs text-foreground transition-colors hover:bg-muted/40"
                        >
                            <PhoneCall className="h-3.5 w-3.5" aria-hidden />
                            Load recording
                        </button>
                    )
                ) : review.recordingId ? (
                    <div className="text-xs text-muted-foreground">Playback permission is required.</div>
                ) : (
                    <div className="text-xs text-muted-foreground">No recording for this call.</div>
                )}
                {review.recordingError ? (
                    <div className="mt-1 text-xs text-red-600 dark:text-red-400" role="alert">
                        Couldn&apos;t load recording: {review.recordingError}
                    </div>
                ) : null}
            </div>

            <div>
                <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                    Transcript
                </div>
                {hasTranscript ? (
                    <pre className="max-h-56 overflow-y-auto whitespace-pre-wrap rounded-md bg-white/70 p-2 font-sans text-xs leading-relaxed text-foreground dark:bg-black/20">
                        {review.transcript}
                    </pre>
                ) : (
                    <div className="text-xs text-muted-foreground">
                        No transcript — no spoken conversation (e.g. voicemail or a quick hang-up).
                    </div>
                )}
            </div>
        </div>
    );
}

export default LiveCallsPanel;
