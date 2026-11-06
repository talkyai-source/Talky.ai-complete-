"use client";

/**
 * The ONE Start / Resume confirmation (production-readiness 3.2), used by the
 * campaign detail page, the campaign list's details modal, "Resume Selected"
 * and the command bar's resume: readiness gate, out-of-minutes check, 2.3
 * calling-schedule notice, first-speaker choice and pacing, then the start
 * request itself.
 *
 * `first_speaker` is not saved on the campaign (it is a field of each start
 * request), so it is chosen here every time, starting on "agent". No other
 * code path starts a campaign.
 */
import { useRef, useState } from "react";
import { Loader2, Play } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { CallingScheduleStartNotice } from "@/components/campaigns/calling-schedule-notice";
import { CampaignReadinessNotice, useCampaignReadiness } from "@/components/campaigns/campaign-readiness";
import { canStartCampaign, notStartedText, outOfMinutesText, runCampaignStarts, type CampaignStartOutcome } from "@/lib/campaign-control";
import { campaignStatusLabel } from "@/lib/campaign-status";
import { dashboardApi, isMinutesExhausted, type Campaign, type MinutesStatus } from "@/lib/dashboard-api";

export type CampaignStartTarget = Pick<Campaign, "id" | "name" | "status" | "calling_config">;

const DEFAULT_BATCH_SIZE = 10;
const DEFAULT_CALL_GAP_SECONDS = 0;

/**
 * @param options.minutes the tenant's minutes status when the host already has
 *   it (null = could not be read, which never blocks). Leave undefined and the
 *   dialog reads it each time it opens.
 * @param options.onFinished runs once every start request has answered, while
 *   the started campaigns still read "Preparing".
 */
export function useCampaignStartDialog(options: {
    minutes?: MinutesStatus | null;
    onFinished?: (outcome: CampaignStartOutcome) => void | Promise<void>;
} = {}) {
    const [targets, setTargets] = useState<CampaignStartTarget[]>([]);
    const [isOpen, setIsOpen] = useState(false);
    const [firstSpeaker, setFirstSpeaker] = useState<"agent" | "user">("agent");
    // Batch size = max calls in flight at once (dialing/ringing/answered). The
    // campaign dials in batches of this size; a new call starts only as an
    // earlier one finishes. Seeded from the campaign's saved setting when known.
    const [batchSize, setBatchSize] = useState<number>(DEFAULT_BATCH_SIZE);
    // Wait time (seconds) between consecutive calls — paces dialing on top of
    // the batch size. 0 = no gap (dial as slots free).
    const [callGap, setCallGap] = useState<number>(DEFAULT_CALL_GAP_SECONDS);
    const [running, setRunning] = useState(false);
    // Asked for a set of campaigns (Resume Selected, command bar): every campaign
    // that cannot start is listed by name, even when the set holds only one.
    const [listRequested, setListRequested] = useState(false);
    // undefined = not answered yet; null = could not be read (never blocks).
    const [readMinutes, setReadMinutes] = useState<MinutesStatus | null | undefined>(undefined);
    const minutesRead = useRef(0);
    const confirming = useRef(false);

    // Only a draft, paused or stopped campaign can start; the others in a set
    // are listed with their status and never checked or sent.
    const eligible = targets.filter((target) => canStartCampaign(target.status));
    const readiness = useCampaignReadiness(eligible.map((target) => target.id), isOpen);
    const minutes = options.minutes !== undefined ? options.minutes : readMinutes;
    const single = targets.length === 1;
    // Pacing is one campaign's own setting. It is shown and sent only from that
    // campaign's own Start / Resume button; a set (Resume Selected, command
    // bar) never shows or sends it, so each campaign keeps its saved pacing.
    const paced = single && !listRequested;
    // Out of plan minutes ⇒ the backend would 402 the start anyway.
    const outOfMinutes = isMinutesExhausted(minutes);
    const minutesReason = outOfMinutes ? outOfMinutesText(minutes) : null;

    const listed = listRequested || !single;
    // One campaign: the readiness answer is that campaign's. Several: each has its own.
    const verdicts = targets.map((target) => {
        if (!canStartCampaign(target.status)) return { target, ready: false, reason: campaignStatusLabel(target.status) };
        const own = single
            ? { ready: readiness.ready, reason: readiness.ready || readiness.checking ? null : readiness.reason }
            : readiness.byId[target.id] ?? { ready: false, reason: null };
        return { target, ready: !outOfMinutes && own.ready, reason: minutesReason ?? own.reason };
    });
    const startable = verdicts.filter((verdict) => verdict.ready).map((verdict) => verdict.target);
    // What the dialog lists by name. Opened for one campaign from its own Start /
    // Resume button, the readiness reason is already the notice above the
    // choices, so only a minutes refusal is listed.
    const skipped = verdicts
        .filter((verdict) => !verdict.ready && verdict.reason && (listed || outOfMinutes))
        .map((verdict) => ({ id: verdict.target.id, name: verdict.target.name, reason: verdict.reason as string }));
    const canConfirm = !running && minutes !== undefined && !readiness.checking && startable.length > 0;
    // The readiness notice: always for one campaign's own button; for a listed
    // set only while a first readiness answer is still awaited.
    const showReadinessNotice = !listed
        || (single ? readiness.checking : eligible.some((target) => readiness.byId[target.id]?.pending ?? true));

    function open(next: CampaignStartTarget[], request: { listSkipped?: boolean } = {}) {
        if (next.length === 0 || running) return;
        setTargets(next);
        setListRequested(request.listSkipped === true);
        // Default back to "agent" every time so a previous choice doesn't
        // silently carry over into the next Start.
        setFirstSpeaker("agent");
        // Seed the pacing controls from the campaign's saved settings if present.
        const cfg = (next.length === 1 ? next[0]!.calling_config : undefined) as
            | { batch_size?: number; call_gap_seconds?: number }
            | null
            | undefined;
        setBatchSize(typeof cfg?.batch_size === "number" && cfg.batch_size >= 0 ? cfg.batch_size : DEFAULT_BATCH_SIZE);
        setCallGap(typeof cfg?.call_gap_seconds === "number" && cfg.call_gap_seconds >= 0 ? cfg.call_gap_seconds : DEFAULT_CALL_GAP_SECONDS);
        if (options.minutes === undefined) {
            const read = ++minutesRead.current;
            setReadMinutes(undefined);
            // Best-effort — a quota hiccup must not block the dialog.
            void dashboardApi.getMinutesStatus().catch(() => null).then((status) => {
                if (minutesRead.current === read) setReadMinutes(status);
            });
        }
        setIsOpen(true);
    }

    function close() {
        if (!running) setIsOpen(false);
    }

    async function confirm() {
        if (!canConfirm || confirming.current) return;
        confirming.current = true;
        setRunning(true);
        const skippedNow = skipped;
        try {
            await runCampaignStarts(
                startable,
                // Pacing is one campaign's setting: for a set it is left out, so
                // each campaign keeps its own saved batch size and gap.
                paced
                    ? { first_speaker: firstSpeaker, batch_size: batchSize, call_gap_seconds: callGap }
                    : { first_speaker: firstSpeaker },
                async (outcome) => {
                    setRunning(false);
                    setIsOpen(false);
                    await options.onFinished?.({ ...outcome, notStarted: [...skippedNow, ...outcome.notStarted] });
                },
            );
        } finally {
            confirming.current = false;
            setRunning(false);
        }
    }

    return {
        isOpen, open, close, confirm, targets, startable, skipped, single, paced, readiness, showReadinessNotice, running, canConfirm,
        firstSpeaker, setFirstSpeaker, batchSize, setBatchSize, callGap, setCallGap,
    };
}

export type CampaignStartDialogState = ReturnType<typeof useCampaignStartDialog>;

export function CampaignStartDialog({ dialog }: { dialog: CampaignStartDialogState }) {
    const { firstSpeaker, setFirstSpeaker, batchSize, setBatchSize, callGap, setCallGap, running } = dialog;
    return (
        <Modal
            open={dialog.isOpen}
            onOpenChange={(next) => {
                if (!next) dialog.close();
            }}
            title="Who speaks first?"
            description="Pick who opens the conversation when the callee answers. The call pipeline pre-warms either way — this only controls whether the agent speaks an opening line or waits for the caller."
            size="sm"
            footer={
                <div className="flex justify-end gap-2">
                    <Button variant="outline" onClick={dialog.close} disabled={running}>
                        Cancel
                    </Button>
                    <Button onClick={() => { void dialog.confirm(); }} disabled={!dialog.canConfirm}>
                        {running ? (
                            <Loader2 className="w-4 h-4 animate-spin" />
                        ) : (
                            <Play className="w-4 h-4" />
                        )}
                        {running ? "Starting…" : "Start campaign"}
                    </Button>
                </div>
            }
        >
            <div className="space-y-2" data-testid="campaign-start-dialog">
                {dialog.showReadinessNotice ? <CampaignReadinessNotice readiness={dialog.readiness} /> : null}
                {dialog.skipped.length > 0 ? (
                    <div
                        role="status"
                        data-testid="campaign-start-skipped"
                        className="min-w-0 space-y-1 break-words rounded-md border border-amber-500/30 bg-amber-500/10 px-2.5 py-1.5 text-sm font-medium text-amber-800 dark:text-amber-300"
                    >
                        {dialog.skipped.map((entry) => (
                            <p key={entry.id}>{notStartedText(entry.name, entry.reason)}</p>
                        ))}
                    </div>
                ) : null}
                {dialog.single ? (
                    <CallingScheduleStartNotice callingConfig={dialog.targets[0]?.calling_config} />
                ) : (
                    dialog.startable.map((target) => (
                        <div key={target.id} className="min-w-0 rounded-lg border border-border p-3">
                            <div className="break-words text-sm font-medium text-foreground">{target.name}</div>
                            <CallingScheduleStartNotice callingConfig={target.calling_config} />
                        </div>
                    ))
                )}
                <label
                    className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors ${
                        firstSpeaker === "agent"
                            ? "border-primary bg-primary/5"
                            : "border-border hover:bg-muted/40"
                    }`}
                >
                    <input
                        type="radio"
                        name="first-speaker"
                        value="agent"
                        checked={firstSpeaker === "agent"}
                        onChange={() => setFirstSpeaker("agent")}
                        className="mt-1"
                    />
                    <div>
                        <div className="text-sm font-medium text-foreground">AI agent speaks first</div>
                        <div className="text-xs text-muted-foreground">
                            Agent plays a pre-synthesized greeting the instant the callee picks up. Best for cold outreach.
                        </div>
                    </div>
                </label>

                <label
                    className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors ${
                        firstSpeaker === "user"
                            ? "border-primary bg-primary/5"
                            : "border-border hover:bg-muted/40"
                    }`}
                >
                    <input
                        type="radio"
                        name="first-speaker"
                        value="user"
                        checked={firstSpeaker === "user"}
                        onChange={() => setFirstSpeaker("user")}
                        className="mt-1"
                    />
                    <div>
                        <div className="text-sm font-medium text-foreground">Caller speaks first</div>
                        <div className="text-xs text-muted-foreground">
                            Agent waits for the callee to say &ldquo;hello&rdquo; before responding. Feels more natural on warm lists.
                        </div>
                    </div>
                </label>

                {dialog.paced ? (
                    <>
                        <div className="mt-3 rounded-lg border border-border p-3">
                            <div className="flex items-center justify-between gap-3">
                                <div>
                                    <div className="text-sm font-medium text-foreground">
                                        Calls at a time (batch size)
                                    </div>
                                    <div className="text-xs text-muted-foreground">
                                        How many calls dial simultaneously. A new call starts only as an
                                        earlier one finishes (answered, no-answer, voicemail, etc.).
                                        Lower = gentler on your trunk; higher = faster throughput.
                                    </div>
                                </div>
                                <input
                                    type="number"
                                    min={1}
                                    max={100}
                                    value={batchSize}
                                    onChange={(e) => {
                                        const n = parseInt(e.target.value, 10);
                                        setBatchSize(Number.isNaN(n) ? 1 : Math.min(100, Math.max(1, n)));
                                    }}
                                    className="w-20 shrink-0 rounded-md border border-border bg-background px-2 py-1 text-center text-sm text-foreground"
                                />
                            </div>
                            <div className="mt-2 flex flex-wrap gap-1.5">
                                {[5, 10, 20, 50].map((n) => (
                                    <button
                                        key={n}
                                        type="button"
                                        onClick={() => setBatchSize(n)}
                                        className={`rounded-md border px-2 py-0.5 text-xs transition-colors ${
                                            batchSize === n
                                                ? "border-primary bg-primary/5 text-foreground"
                                                : "border-border text-muted-foreground hover:bg-muted/40"
                                        }`}
                                    >
                                        {n}
                                    </button>
                                ))}
                            </div>
                        </div>

                        <div className="mt-3 rounded-lg border border-border p-3">
                            <div className="flex items-center justify-between gap-3">
                                <div>
                                    <div className="text-sm font-medium text-foreground">
                                        Wait between calls
                                    </div>
                                    <div className="text-xs text-muted-foreground">
                                        Minimum gap before the next call is dialed, so calls go out on a
                                        steady cadence instead of all at once. Applies on top of the batch
                                        size. Set to 0 for no wait.
                                    </div>
                                </div>
                                <div className="flex shrink-0 items-center gap-1">
                                    <input
                                        type="number"
                                        min={0}
                                        max={60}
                                        aria-label="Minutes"
                                        value={Math.floor(callGap / 60)}
                                        onChange={(e) => {
                                            const m = parseInt(e.target.value, 10);
                                            const mins = Number.isNaN(m) ? 0 : Math.min(60, Math.max(0, m));
                                            setCallGap(Math.min(3600, mins * 60 + (callGap % 60)));
                                        }}
                                        className="w-14 rounded-md border border-border bg-background px-2 py-1 text-center text-sm text-foreground"
                                    />
                                    <span className="text-xs text-muted-foreground">min</span>
                                    <input
                                        type="number"
                                        min={0}
                                        max={59}
                                        aria-label="Seconds"
                                        value={callGap % 60}
                                        onChange={(e) => {
                                            const s = parseInt(e.target.value, 10);
                                            const secs = Number.isNaN(s) ? 0 : Math.min(59, Math.max(0, s));
                                            setCallGap(Math.min(3600, Math.floor(callGap / 60) * 60 + secs));
                                        }}
                                        className="w-14 rounded-md border border-border bg-background px-2 py-1 text-center text-sm text-foreground"
                                    />
                                    <span className="text-xs text-muted-foreground">sec</span>
                                </div>
                            </div>
                            <div className="mt-2 flex items-center gap-2">
                                <div className="flex flex-wrap gap-1.5">
                                    {[
                                        { label: "None", v: 0 },
                                        { label: "30s", v: 30 },
                                        { label: "1m", v: 60 },
                                        { label: "2m", v: 120 },
                                        { label: "5m", v: 300 },
                                    ].map((p) => (
                                        <button
                                            key={p.v}
                                            type="button"
                                            onClick={() => setCallGap(p.v)}
                                            className={`rounded-md border px-2 py-0.5 text-xs transition-colors ${
                                                callGap === p.v
                                                    ? "border-primary bg-primary/5 text-foreground"
                                                    : "border-border text-muted-foreground hover:bg-muted/40"
                                            }`}
                                        >
                                            {p.label}
                                        </button>
                                    ))}
                                </div>
                                {callGap > 0 && (
                                    <span className="ml-auto text-xs text-muted-foreground">
                                        = {Math.floor(callGap / 60) > 0 ? `${Math.floor(callGap / 60)}m ` : ""}
                                        {callGap % 60 > 0 ? `${callGap % 60}s` : (Math.floor(callGap / 60) > 0 ? "" : "0s")}
                                    </span>
                                )}
                            </div>
                        </div>
                    </>
                ) : null}
            </div>
        </Modal>
    );
}
