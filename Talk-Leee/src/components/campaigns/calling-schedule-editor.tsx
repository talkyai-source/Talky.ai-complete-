"use client";

/**
 * Per-campaign calling schedule editor (Phase 3c-v2).
 *
 * Lets the client set the timezone, daily calling window, and allowed days
 * for a campaign — plus a "call anytime" override. Shows a live, non-blocking
 * warning when right now is outside the chosen window. It never prevents the
 * client from doing anything; it only informs.
 *
 * It shows exactly the values it is given: a field with no value reads
 * "Not set" and is never replaced by the browser zone or 09:00/19:00, so
 * what the user sees is what will be saved (production-readiness 2.3).
 */
import { useMemo } from "react";
import { AlertTriangle, Clock } from "lucide-react";

import { Select } from "@/components/ui/select";
import type { CampaignCallingSchedule } from "@/lib/dashboard-api";
import { callingHoursInverted, checkCallingWindow, HOURS_ORDER_ERROR } from "@/lib/calling-window";
import {
    CALLING_HOURS_RULE_TEXT,
    DAY_LABELS,
    FIELD_NOT_SET_TEXT,
    OUTSIDE_HOURS_TEXT,
    scheduleMissingWarning,
} from "@/lib/calling-schedule-text";

// A short, friendly timezone list covering the common US zones + a few global
// ones; "My timezone" resolves the browser's. Free-text isn't needed —
// these cover the vast majority of outbound use.
const TZ_OPTIONS: Array<{ value: string; label: string }> = [
    { value: "America/New_York", label: "Eastern (New York)" },
    { value: "America/Chicago", label: "Central (Chicago)" },
    { value: "America/Denver", label: "Mountain (Denver)" },
    { value: "America/Los_Angeles", label: "Pacific (Los Angeles)" },
    { value: "America/Phoenix", label: "Arizona (Phoenix)" },
    { value: "Europe/London", label: "UK (London)" },
    { value: "Europe/Berlin", label: "Central Europe (Berlin)" },
    { value: "Asia/Karachi", label: "Pakistan (Karachi)" },
    { value: "Asia/Kolkata", label: "India (Kolkata)" },
    { value: "Asia/Dubai", label: "Gulf (Dubai)" },
    { value: "UTC", label: "UTC" },
];

const WARNING_CLASS =
    "flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300";

export function CallingScheduleEditor({
    value,
    onChange,
}: {
    value: CampaignCallingSchedule;
    onChange: (next: CampaignCallingSchedule) => void;
}) {
    const set = (patch: Partial<CampaignCallingSchedule>) => onChange({ ...value, ...patch });

    const browserTz = useMemo(
        () => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
        [],
    );
    const tz = value.timezone ?? "";
    const days = value.allowed_days ?? [];
    const missingWarning = scheduleMissingWarning(value);

    const warning = useMemo(() => checkCallingWindow(value), [value]);

    const toggleDay = (d: number) => {
        const has = days.includes(d);
        const next = has ? days.filter((x) => x !== d) : [...days, d].sort((a, b) => a - b);
        set({ allowed_days: next });
    };

    return (
        <div className="space-y-4">
            <div className="flex items-center gap-2 text-sm font-semibold">
                <Clock className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
                Calling hours
            </div>
            <p className="text-xs text-muted-foreground">{CALLING_HOURS_RULE_TEXT}</p>

            {/* Timezone */}
            <div>
                <label className="mb-1 block text-xs font-medium text-muted-foreground">Timezone</label>
                <Select
                    ariaLabel="Calling timezone"
                    value={tz}
                    onChange={(next) => set({ timezone: next })}
                    selectClassName="h-auto rounded-lg border-border px-3 py-2 pr-9 text-sm"
                >
                    {!tz && <option value="" disabled>{FIELD_NOT_SET_TEXT}</option>}
                    {!TZ_OPTIONS.some((o) => o.value === browserTz) && (
                        <option value={browserTz}>{`My timezone (${browserTz})`}</option>
                    )}
                    {tz && tz !== browserTz && !TZ_OPTIONS.some((o) => o.value === tz) && (
                        <option value={tz}>{tz}</option>
                    )}
                    {TZ_OPTIONS.map((o) => (
                        <option key={o.value} value={o.value}>{o.label}</option>
                    ))}
                </Select>
            </div>

            {/* Window */}
            <div className="grid grid-cols-2 gap-3">
                <div>
                    <label className="mb-1 block text-xs font-medium text-muted-foreground">Start</label>
                    <input
                        type="time"
                        aria-label="Calling start time"
                        value={value.time_window_start ?? ""}
                        onChange={(e) => set({ time_window_start: e.target.value })}
                        className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
                    />
                    {!value.time_window_start && (
                        <p className="mt-1 text-xs text-muted-foreground" data-testid="calling-start-not-set">{FIELD_NOT_SET_TEXT}</p>
                    )}
                </div>
                <div>
                    <label className="mb-1 block text-xs font-medium text-muted-foreground">End</label>
                    <input
                        type="time"
                        aria-label="Calling end time"
                        value={value.time_window_end ?? ""}
                        onChange={(e) => set({ time_window_end: e.target.value })}
                        className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
                    />
                    {!value.time_window_end && (
                        <p className="mt-1 text-xs text-muted-foreground" data-testid="calling-end-not-set">{FIELD_NOT_SET_TEXT}</p>
                    )}
                </div>
            </div>

            {callingHoursInverted(value) && (
                <p role="alert" className="text-xs text-destructive" data-testid="calling-hours-order-error">{HOURS_ORDER_ERROR}</p>
            )}

            {/* Days */}
            <div>
                <label className="mb-1 block text-xs font-medium text-muted-foreground">Days</label>
                <div className="flex flex-wrap gap-1.5" role="group" aria-label="Calling days">
                    {DAY_LABELS.map((d, i) => {
                        const on = days.includes(i);
                        return (
                            <button
                                key={d}
                                type="button"
                                aria-pressed={on}
                                onClick={() => toggleDay(i)}
                                className={`rounded-md px-2.5 py-1 text-xs font-medium ${on ? "bg-emerald-600 text-white" : "border border-border text-muted-foreground hover:text-foreground"}`}
                            >
                                {d}
                            </button>
                        );
                    })}
                </div>
                {days.length === 0 && (
                    <p className="mt-1 text-xs text-muted-foreground" data-testid="calling-days-not-set">{FIELD_NOT_SET_TEXT}</p>
                )}
            </div>

            {/* Override */}
            <label className="flex items-start gap-2 text-sm">
                <input
                    type="checkbox"
                    checked={!!value.ignore_schedule}
                    onChange={(e) => set({ ignore_schedule: e.target.checked })}
                    className="mt-0.5 h-4 w-4 accent-emerald-600"
                />
                <span>
                    <span className="font-medium">Call anytime (ignore these hours)</span>
                    <span className="block text-xs text-muted-foreground">{OUTSIDE_HOURS_TEXT}</span>
                </span>
            </label>

            {/* Missing scheduling information (advisory only) */}
            {missingWarning && (
                <div className={WARNING_CLASS} role="status" data-testid="calling-schedule-missing">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                    <span>{missingWarning}</span>
                </div>
            )}

            {/* Live out-of-hours warning (advisory only) */}
            {warning.outside && (
                <div className={WARNING_CLASS}>
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                    <span>
                        {warning.message}{" "}
                        {OUTSIDE_HOURS_TEXT}
                    </span>
                </div>
            )}
        </div>
    );
}

export default CallingScheduleEditor;
