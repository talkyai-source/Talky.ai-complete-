import { AlertTriangle, CheckCircle2, CircleDashed, ShieldCheck, XCircle } from "lucide-react";

import type { InboundReadiness } from "@/lib/inbound-api";
import { cn } from "@/lib/utils";

const STATUS_STYLES: Record<string, string> = {
    active: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
    paused: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
    archived: "border-slate-500/30 bg-slate-500/10 text-slate-700 dark:text-slate-300",
    draft: "border-border bg-muted text-muted-foreground",
};

function humanize(value: string): string {
    return value.replace(/_/g, " ").replace(/\b\w/g, (character) => character.toUpperCase());
}

export function InboundStatusBadge({ status }: { status: string }) {
    const normalized = status.trim().toLowerCase();
    return (
        <span
            className={cn(
                "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold",
                STATUS_STYLES[normalized] ?? STATUS_STYLES.draft,
            )}
        >
            {humanize(normalized || "draft")}
        </span>
    );
}

export function ReadinessBadge({ readiness }: { readiness: InboundReadiness }) {
    const passed = readiness.checks.filter((check) => check.passed).length;
    const blockedCount = readiness.blockers.length;
    return (
        <span
            className={cn(
                "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold",
                readiness.ready
                    ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                    : "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
            )}
            title={readiness.ready
                ? `${passed} of ${readiness.checks.length} server readiness checks passed`
                : `${blockedCount} activation ${blockedCount === 1 ? "blocker" : "blockers"}`}
        >
            {readiness.ready ? <ShieldCheck className="h-3.5 w-3.5" aria-hidden /> : <AlertTriangle className="h-3.5 w-3.5" aria-hidden />}
            {readiness.ready ? "Ready" : `${blockedCount} ${blockedCount === 1 ? "blocker" : "blockers"}`}
        </span>
    );
}

export function InboundReadinessChecklist({ readiness }: { readiness: InboundReadiness }) {
    return (
        <div className="space-y-3" aria-label="Server readiness checks">
            <div
                className={cn(
                    "flex items-start gap-3 rounded-xl border p-4",
                    readiness.ready
                        ? "border-emerald-500/25 bg-emerald-500/5"
                        : "border-amber-500/25 bg-amber-500/5",
                )}
            >
                {readiness.ready ? (
                    <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden />
                ) : (
                    <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden />
                )}
                <div>
                    <p className="text-sm font-semibold text-foreground">
                        {readiness.ready ? "Safe to request activation" : "Activation is blocked"}
                    </p>
                    <p className="mt-0.5 text-sm text-muted-foreground">
                        {readiness.ready
                            ? "The server has verified the current DID, agent and telephony configuration."
                            : "Resolve every failed check below. The server re-checks everything when activation is requested."}
                    </p>
                </div>
            </div>

            {readiness.checks.length === 0 && readiness.blockers.length === 0 ? (
                <div className="flex items-start gap-3 rounded-xl border border-border bg-muted/30 p-4">
                    <CircleDashed className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" aria-hidden />
                    <div>
                        <p className="text-sm font-semibold text-foreground">Checks have not run yet</p>
                        <p className="mt-0.5 text-sm text-muted-foreground">Save the configuration or refresh this page to request a server evaluation.</p>
                    </div>
                </div>
            ) : readiness.checks.length > 0 ? (
                <ul className="space-y-2">
                    {readiness.checks.map((check) => (
                        <li key={check.key} className="flex items-start gap-3 rounded-xl border border-border bg-background p-3">
                            {check.passed ? (
                                <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden />
                            ) : (
                                <XCircle className="mt-0.5 h-5 w-5 shrink-0 text-red-600 dark:text-red-400" aria-hidden />
                            )}
                            <div className="min-w-0">
                                <p className="text-sm font-semibold text-foreground">{check.label}</p>
                                <p className="mt-0.5 text-sm text-muted-foreground">{check.detail}</p>
                            </div>
                        </li>
                    ))}
                </ul>
            ) : null}

            {readiness.blockers.length > 0 ? (
                <div>
                    <h3 className="mb-2 text-sm font-semibold text-foreground">What needs attention</h3>
                    <ul className="space-y-2">
                        {readiness.blockers.map((blocker) => (
                            <li key={blocker.code} className="rounded-xl border border-red-500/20 bg-red-500/5 p-3">
                                <div className="flex items-start gap-3">
                                    <XCircle className="mt-0.5 h-5 w-5 shrink-0 text-red-600 dark:text-red-400" aria-hidden />
                                    <div className="min-w-0">
                                        <p className="text-sm font-semibold text-foreground">{blocker.message}</p>
                                        <p className="mt-1 text-sm text-muted-foreground">{blocker.remediation}</p>
                                        <p className="mt-1 font-mono text-[11px] text-muted-foreground">{blocker.code}</p>
                                    </div>
                                </div>
                            </li>
                        ))}
                    </ul>
                </div>
            ) : null}
        </div>
    );
}
