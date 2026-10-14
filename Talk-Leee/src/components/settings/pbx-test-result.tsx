"use client";

import { CheckCircle2, Loader2, MinusCircle, X, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import type { SipTestStep, SipTrunkTestResponse } from "@/lib/telephony-api";
import { formatRelativeTime } from "@/components/settings/pbx-line-status";

/** What the card knows about the test it ran for one line. */
export type LineTestState =
    | { phase: "running" }
    | { phase: "done"; result: SipTrunkTestResponse }
    | { phase: "error"; message: string };

type Tone = "good" | "warn" | "bad";

const TONE_CLASSES: Record<Tone, { box: string; verdict: string }> = {
    good: { box: "border-emerald-500/30 bg-emerald-500/10", verdict: "text-emerald-700 dark:text-emerald-400" },
    warn: { box: "border-amber-500/30 bg-amber-500/10", verdict: "text-amber-700 dark:text-amber-400" },
    bad: { box: "border-red-500/30 bg-red-500/10", verdict: "text-red-700 dark:text-red-400" },
};

function toneOf(result: SipTrunkTestResponse): Tone {
    switch (result.status) {
        case "connected":
        case "reachable":
            return "good";
        case "not_registered":
            // A line that is switched off passes with ok: true ("turn it on to register");
            // one that should be signed in but is not is a real problem.
            return result.ok ? "good" : "warn";
        case "credentials_rejected":
        case "unreachable":
            return "bad";
        default:
            return result.ok ? "good" : result.inconclusive ? "warn" : "bad";
    }
}

/** Older saved results have no verdict sentence; say something honest from what they do have. */
function verdictOf(result: SipTrunkTestResponse): string {
    const verdict = result.verdict?.trim();
    if (verdict) return verdict;
    if (result.ok) return "Your provider answered.";
    return result.detail?.trim() || result.error?.trim() || (result.inconclusive ? "The check was inconclusive." : "We couldn't reach your provider.");
}

function stepsOf(result: SipTrunkTestResponse): SipTestStep[] {
    if (Array.isArray(result.steps) && result.steps.length > 0) return result.steps;
    return [{
        key: "reachability",
        ok: result.ok ? true : result.inconclusive ? null : false,
        label: "Reaching your provider",
        detail: result.detail?.trim() || result.error?.trim() || "",
        sip_code: result.sip_code,
    }];
}

function codeOf(value: string | number | null | undefined): string | null {
    if (value === undefined || value === null) return null;
    const code = String(value).trim();
    return code || null;
}

const STEP_WORD = { true: "Passed", false: "Failed", null: "Skipped" } as const;

function StepIcon({ ok }: { ok: boolean | null }) {
    if (ok === true) return <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden />;
    if (ok === false) return <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-600 dark:text-red-400" aria-hidden />;
    return <MinusCircle className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />;
}

function DismissButton({ lineName, onDismiss }: { lineName: string; onDismiss: () => void }) {
    return (
        <button
            type="button"
            onClick={onDismiss}
            aria-label={`Dismiss test result for ${lineName}`}
            className="-m-1 shrink-0 rounded p-1 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
            <X className="h-4 w-4" aria-hidden />
        </button>
    );
}

/** The outcome of pressing Test: verdict first, then every check that ran. */
export function TestResultPanel({
    lineName,
    state,
    now,
    onDismiss,
}: {
    lineName: string;
    state: LineTestState;
    now: number;
    onDismiss: () => void;
}) {
    if (state.phase === "running") {
        return (
            <div role="status" className="mt-3 flex items-center gap-2 rounded-lg border border-border bg-muted/30 p-3 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden />
                Checking {lineName}…
            </div>
        );
    }

    if (state.phase === "error") {
        return (
            <div role="alert" className={cn("mt-3 rounded-lg border p-3 text-sm", TONE_CLASSES.bad.box)}>
                <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                        <div className={cn("font-semibold", TONE_CLASSES.bad.verdict)}>The test didn&apos;t run</div>
                        <div className="mt-0.5 text-foreground wrap-anywhere">{state.message}</div>
                    </div>
                    <DismissButton lineName={lineName} onDismiss={onDismiss} />
                </div>
            </div>
        );
    }

    const { result } = state;
    const tone = TONE_CLASSES[toneOf(result)];
    const steps = stepsOf(result);
    const topCode = codeOf(result.sip_code);
    const stepCodes = steps.map((step) => codeOf(step.sip_code));
    const tested = formatRelativeTime(result.tested_at, now);
    const latency = typeof result.latency_ms === "number" && result.latency_ms > 0 ? `${result.latency_ms} ms` : null;
    const footer = [tested ? `Tested ${tested}` : null, latency, topCode && !stepCodes.includes(topCode) ? `SIP ${topCode}` : null]
        .filter(Boolean)
        .join(" · ");

    return (
        <section aria-label={`Test result for ${lineName}`} className={cn("mt-3 rounded-lg border p-3", tone.box)}>
            <div className="flex items-start justify-between gap-2">
                <p role="status" className={cn("text-sm font-semibold wrap-anywhere", tone.verdict)}>{verdictOf(result)}</p>
                <DismissButton lineName={lineName} onDismiss={onDismiss} />
            </div>
            <ul className="mt-2 space-y-2">
                {steps.map((step, index) => {
                    const code = stepCodes[index];
                    return (
                        <li key={`${step.key}-${index}`} className="flex items-start gap-2" data-step={step.key}>
                            <StepIcon ok={step.ok} />
                            <div className="min-w-0">
                                <div className="text-sm font-medium text-foreground">
                                    {step.label}
                                    <span className="sr-only"> ({STEP_WORD[String(step.ok) as keyof typeof STEP_WORD] ?? "Skipped"})</span>
                                </div>
                                {step.detail ? <div className="text-xs text-muted-foreground wrap-anywhere">{step.detail}</div> : null}
                                {code ? <div className="text-[11px] text-muted-foreground">SIP {code}</div> : null}
                            </div>
                        </li>
                    );
                })}
            </ul>
            {footer ? <p className="mt-2 text-[11px] text-muted-foreground">{footer}</p> : null}
        </section>
    );
}
