"use client";

import { useSyncExternalStore } from "react";
import { Activity, AlertCircle, CheckCircle2, CircleDashed, Loader2, Pencil, PhoneCall, PhoneOutgoing, PowerOff, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import type { SipTrunkRow } from "@/lib/telephony-api";
import { formatServer } from "@/components/settings/pbx-address";
import { deriveLineStatus, formatRelativeTime, type LineStatus, type LineStatusKind } from "@/components/settings/pbx-line-status";
import { TestResultPanel, type LineTestState } from "@/components/settings/pbx-test-result";

// --- A shared clock, so "Last checked 2 minutes ago" keeps moving between refetches ---

const CLOCK_TICK_MS = 15_000;
const subscribeToClock = (onChange: () => void) => {
    const id = setInterval(onChange, CLOCK_TICK_MS);
    return () => clearInterval(id);
};
// Bucketed so the snapshot is stable between ticks; 0 on the server and during hydration.
const readClock = () => Math.floor(Date.now() / CLOCK_TICK_MS) * CLOCK_TICK_MS;
const readServerClock = () => 0;

/** Milliseconds since the epoch, refreshed every 15 s; 0 until the client has mounted. */
export function useNow(): number {
    return useSyncExternalStore(subscribeToClock, readClock, readServerClock);
}

// --- Status pill ---

const PILL: Record<LineStatusKind, { className: string; Icon: typeof CheckCircle2; spin?: boolean }> = {
    connected: { className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400", Icon: CheckCircle2 },
    connecting: { className: "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-400", Icon: Loader2, spin: true },
    attention: { className: "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400", Icon: AlertCircle },
    off: { className: "border-gray-500/30 bg-gray-500/10 text-gray-700 dark:text-gray-400", Icon: PowerOff },
    unchecked: { className: "border-border bg-muted text-muted-foreground", Icon: CircleDashed },
};

function StatusPill({ status }: { status: LineStatus }) {
    const { className, Icon, spin } = PILL[status.kind];
    return (
        <span
            data-status={status.kind}
            className={cn("inline-flex w-fit items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold", className)}
        >
            <Icon className={cn("h-3.5 w-3.5", spin && "animate-spin motion-reduce:animate-none")} aria-hidden />
            {status.label}
        </span>
    );
}

// --- Plain wording for the line's settings ---

function directionText(direction: SipTrunkRow["direction"]): string {
    if (direction === "inbound") return "Receives calls only";
    if (direction === "outbound") return "Makes calls only";
    return "Makes and receives calls";
}

function signInText(trunk: SipTrunkRow): string {
    if (!trunk.auth_configured) return "Signs in by IP address";
    return trunk.auth_username ? `Username: ${trunk.auth_username}` : "Signs in with a username and password";
}

export interface PbxLineCardProps {
    trunk: SipTrunkRow;
    now: number;
    /** The dialer's own routing decision picked this line for outbound calls. */
    usedForOutbound: boolean;
    test: LineTestState | undefined;
    /** This line's On/Off change is in flight. */
    toggling: boolean;
    /** Some delete is in flight; deleting is one at a time. */
    deleteBusy: boolean;
    onTest: (trunk: SipTrunkRow) => void;
    onEdit: (trunk: SipTrunkRow) => void;
    onToggle: (trunk: SipTrunkRow) => void;
    onDelete: (trunk: SipTrunkRow) => void;
    onDismissTest: (trunk: SipTrunkRow) => void;
}

/** One connected PBX line: who it is, whether it works, and what you can do with it. */
export function PbxLineCard({
    trunk,
    now,
    usedForOutbound,
    test,
    toggling,
    deleteBusy,
    onTest,
    onEdit,
    onToggle,
    onDelete,
    onDismissTest,
}: PbxLineCardProps) {
    const status = deriveLineStatus(trunk);
    const callerId = typeof trunk.metadata?.caller_id === "string" ? trunk.metadata.caller_id : "";
    const checked = formatRelativeTime(status.checkedAt, now);
    const checkedLine = [checked ? `Last checked ${checked}` : null, status.sipCode ? `SIP ${status.sipCode}` : null]
        .filter(Boolean)
        .join(" · ");
    const testing = test?.phase === "running";
    const server = formatServer({ host: trunk.sip_domain, port: trunk.port, transport: trunk.transport });
    const busy = toggling || deleteBusy;

    return (
        <li data-line-id={trunk.id} data-status={status.kind} className="rounded-xl border border-border bg-card/50 p-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0 space-y-1">
                    <div className="flex items-center gap-2">
                        <PhoneCall className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                        <h3 className="min-w-0 text-sm font-semibold text-foreground wrap-anywhere">{trunk.trunk_name}</h3>
                    </div>
                    <p className="font-mono text-xs text-muted-foreground wrap-anywhere">{server}</p>
                    <p className="text-xs text-muted-foreground wrap-anywhere">{signInText(trunk)}</p>
                    <p className="text-xs text-muted-foreground">{directionText(trunk.direction)}</p>
                    {callerId ? <p className="text-xs text-muted-foreground wrap-anywhere">Number shown on calls: {callerId}</p> : null}
                    {usedForOutbound ? (
                        <p className="inline-flex w-fit items-center gap-1 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-[11px] font-semibold text-emerald-700 dark:text-emerald-400">
                            <PhoneOutgoing className="h-3 w-3" aria-hidden />
                            Outbound calls use this line
                        </p>
                    ) : null}
                </div>
                <div className="flex min-w-0 flex-col gap-1 sm:max-w-[45%] sm:items-end sm:text-right">
                    <StatusPill status={status} />
                    {status.detail ? <p className="text-xs text-foreground">{status.detail}</p> : null}
                    {status.technical ? <p className="text-[11px] text-muted-foreground wrap-anywhere">{status.technical}</p> : null}
                    {checkedLine ? <p className="text-[11px] text-muted-foreground">{checkedLine}</p> : null}
                </div>
            </div>

            {test ? (
                <TestResultPanel lineName={trunk.trunk_name} state={test} now={now} onDismiss={() => onDismissTest(trunk)} />
            ) : null}

            <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-3">
                <Button
                    size="sm"
                    variant="outline"
                    onClick={() => onTest(trunk)}
                    disabled={testing}
                    aria-label={`Test ${trunk.trunk_name}`}
                    title="Check the connection to your provider"
                >
                    {testing ? (
                        <><Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden /> Testing…</>
                    ) : (
                        <><Activity className="h-3.5 w-3.5" aria-hidden /> Test</>
                    )}
                </Button>
                <Button size="sm" variant="outline" onClick={() => onEdit(trunk)} aria-label={`Edit ${trunk.trunk_name}`}>
                    <Pencil className="h-3.5 w-3.5" aria-hidden /> Edit
                </Button>
                <div className="ml-auto flex flex-wrap items-center gap-2">
                    <label className="inline-flex cursor-pointer items-center gap-2 text-xs text-muted-foreground">
                        Use this line
                        {toggling ? <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden /> : null}
                        <Switch
                            checked={trunk.is_active}
                            onCheckedChange={() => onToggle(trunk)}
                            ariaLabel={`Use this line (${trunk.trunk_name})`}
                            disabled={busy}
                        />
                    </label>
                    <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => onDelete(trunk)}
                        disabled={busy}
                        aria-label={`Delete ${trunk.trunk_name}`}
                        className="text-destructive hover:text-destructive"
                    >
                        <Trash2 className="h-3.5 w-3.5" aria-hidden /> Delete
                    </Button>
                </div>
            </div>
        </li>
    );
}
