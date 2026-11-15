"use client";

/**
 * Read-only calling-schedule block for the campaign detail page's Start
 * dialog (production-readiness 2.3). Shows the hours the server enforces
 * (`effective_schedule` of GET /campaigns/{id}) when it sent them, otherwise
 * ONLY the saved `calling_config`:
 * the "Calling schedule:" line, the rule the dialer enforces, the
 * missing-information warning for an incomplete schedule, and the live
 * out-of-hours warning. Advisory only; it never blocks Start.
 */
import { AlertTriangle, Loader2 } from "lucide-react";

import type { CampaignCallingSchedule, CampaignEffectiveSchedule } from "@/lib/dashboard-api";
import { checkCallingWindow } from "@/lib/calling-window";
import {
    CALLING_HOURS_RULE_TEXT,
    OUTSIDE_HOURS_TEXT,
    SCHEDULE_NOT_SET_TEXT,
    enforcedSchedule,
    formatCallingSchedule,
    scheduleMissingWarning,
} from "@/lib/calling-schedule-text";

const WARNING_CLASS =
    "mb-2 flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300";

/** The read-only "Calling schedule: …" line, or the exact Not set text when
 *  nothing is saved. Shown in the campaign header and in the Start dialog. */
export function CallingScheduleLine({
    callingConfig, effectiveSchedule, testId = "calling-schedule-line", className = "text-sm text-muted-foreground",
}: {
    callingConfig: CampaignCallingSchedule | null | undefined;
    /** What the server enforces (GET /campaigns/{id}); shown in place of the saved values when present. */
    effectiveSchedule?: CampaignEffectiveSchedule | null;
    testId?: string;
    className?: string;
}) {
    const line = formatCallingSchedule(enforcedSchedule(effectiveSchedule) ?? callingConfig);
    return (
        <p className={className} data-testid={testId}>
            {line ? (
                <>
                    <span className="font-medium text-foreground">Calling schedule:</span>{" "}{line}
                </>
            ) : SCHEDULE_NOT_SET_TEXT}
        </p>
    );
}

export function CallingScheduleStartNotice({
    callingConfig, effectiveSchedule, reading = false,
}: {
    callingConfig: CampaignCallingSchedule | null | undefined;
    /** What the server enforces (GET /campaigns/{id}); null or absent = the saved values, as before. */
    effectiveSchedule?: CampaignEffectiveSchedule | null;
    /** The read of the enforced hours is still running. */
    reading?: boolean;
}) {
    const missing = scheduleMissingWarning(callingConfig);
    // The out-of-hours warning is worked out from the hours shown on the line above.
    const shown = enforcedSchedule(effectiveSchedule) ?? callingConfig;
    const win = !reading && shown ? checkCallingWindow(shown) : null;
    return (
        <>
            {reading ? (
                <div role="status" aria-busy="true" data-testid="calling-schedule-reading" className="flex h-5 items-center text-muted-foreground">
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                </div>
            ) : (
                <CallingScheduleLine callingConfig={callingConfig} effectiveSchedule={effectiveSchedule} />
            )}
            <p className="text-xs text-muted-foreground">{CALLING_HOURS_RULE_TEXT}</p>
            {missing && (
                <div className={WARNING_CLASS} role="status" data-testid="calling-schedule-missing">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                    <span>{missing}</span>
                </div>
            )}
            {win?.outside && (
                <div className={WARNING_CLASS}>
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                    <span>
                        {win.message}{" "}
                        {OUTSIDE_HOURS_TEXT}
                    </span>
                </div>
            )}
        </>
    );
}

export default CallingScheduleStartNotice;
