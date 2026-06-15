"use client";

/**
 * Per-campaign calling schedule editor (Phase 3c-v2).
 *
 * Lets the client set the timezone, daily calling window, and allowed days
 * for a campaign — plus a "call anytime" override. Shows a live, non-blocking
 * warning when right now is outside the chosen window. It never prevents the
 * client from doing anything; it only informs.
 */
import { useMemo } from "react";
import { AlertTriangle, Clock } from "lucide-react";

import type { CampaignCallingSchedule } from "@/lib/dashboard-api";
import { checkCallingWindow } from "@/lib/calling-window";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

// A short, friendly timezone list covering the common US zones + a few global
// ones; "Use my timezone" resolves the browser's. Free-text isn't needed —
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
    const tz = value.timezone || browserTz;
    const days = value.allowed_days && value.allowed_days.length ? value.allowed_days : [0, 1, 2, 3, 4];

    const warning = useMemo(
        () => checkCallingWindow({ ...value, timezone: tz }),
        [value, tz],
    );

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

            {/* Timezone */}
            <div>
                <label className="mb-1 block text-xs font-medium text-muted-foreground">Timezone</label>
                <select
                    value={tz}
                    onChange={(e) => set({ timezone: e.target.value })}
                    className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
                >
                    {!TZ_OPTIONS.some((o) => o.value === browserTz) && (
                        <option value={browserTz}>My timezone ({browserTz})</option>
                    )}
                    {TZ_OPTIONS.map((o) => (
                        <option key={o.value} value={o.value}>{o.label}</option>
                    ))}
                </select>
            </div>

            {/* Window */}
            <div className="grid grid-cols-2 gap-3">
                <div>
                    <label className="mb-1 block text-xs font-medium text-muted-foreground">Start</label>
                    <input
                        type="time"
                        value={value.time_window_start || "09:00"}
                        onChange={(e) => set({ time_window_start: e.target.value })}
                        className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
                    />
                </div>
                <div>
                    <label className="mb-1 block text-xs font-medium text-muted-foreground">End</label>
                    <input
                        type="time"
                        value={value.time_window_end || "19:00"}
                        onChange={(e) => set({ time_window_end: e.target.value })}
                        className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
                    />
                </div>
            </div>

            {/* Days */}
            <div>
                <label className="mb-1 block text-xs font-medium text-muted-foreground">Days</label>
                <div className="flex flex-wrap gap-1.5">
                    {DAYS.map((d, i) => {
                        const on = days.includes(i);
                        return (
                            <button
                                key={d}
                                type="button"
                                onClick={() => toggleDay(i)}
                                className={`rounded-md px-2.5 py-1 text-xs font-medium ${on ? "bg-emerald-600 text-white" : "border border-border text-muted-foreground hover:text-foreground"}`}
                            >
                                {d}
                            </button>
                        );
                    })}
                </div>
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
                    <span className="block text-xs text-muted-foreground">
                        The dialer will call regardless of the window above. You&apos;ll still see warnings.
                    </span>
                </span>
            </label>

            {/* Live out-of-hours warning (advisory only) */}
            {warning.outside && (
                <div className="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                    <span>
                        {warning.message}{" "}
                        {value.ignore_schedule
                            ? "Calls will still go out because “call anytime” is on."
                            : "Calls will wait until the window opens — turn on “call anytime” to dial now."}
                    </span>
                </div>
            )}
        </div>
    );
}

export default CallingScheduleEditor;
