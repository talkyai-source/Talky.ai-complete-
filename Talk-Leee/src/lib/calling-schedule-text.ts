// Texts and pure helpers for a campaign's outbound calling schedule
// (production-readiness 2.3). Used by the schedule editor, the classic
// campaign form and the campaign detail page so every surface shows the
// same saved values and the same rule the dialer enforces
// (backend/app/domain/models/calling_rules.py: per-lead timezone first,
// then the campaign timezone; tenant default 09:00–19:00 UTC, Mon–Fri).

import type { CampaignCallingSchedule } from "@/lib/dashboard-api";

export const DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;

export const CALLING_HOURS_RULE_TEXT =
    "Calling hours are checked in each contact's own timezone when it is known; otherwise in this campaign's timezone.";

export const OUTSIDE_HOURS_TEXT =
    "Calling outside these hours depends on platform settings and is not guaranteed.";

export const SCHEDULE_NOT_SET_TEXT =
    "Not set. The default of 09:00–19:00 UTC, Monday–Friday applies unless your account has its own default.";

export const FIELD_NOT_SET_TEXT = "Not set";

/** Browser-local starting values for a NEW campaign. Every value here is
 *  shown in the editor and therefore sent on save; nothing is implied. */
export function defaultCallingSchedule(): CampaignCallingSchedule {
    let timezone = "UTC";
    try {
        timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    } catch {
        // Keep UTC when the runtime has no zone information.
    }
    return {
        timezone,
        time_window_start: "09:00",
        time_window_end: "19:00",
        allowed_days: [0, 1, 2, 3, 4],
        ignore_schedule: false,
    };
}

function hasText(value: string | null | undefined): value is string {
    return typeof value === "string" && value.trim() !== "";
}

function hasDays(value: number[] | null | undefined): value is number[] {
    return Array.isArray(value) && value.length > 0;
}

/** The request body for `calling_schedule`: all four fields are always
 *  present (null when unset) so an edit replaces the saved values rather
 *  than leaving a stale one behind. An empty days list is sent as null —
 *  the backend would otherwise store `[]` and dial on no day at all. */
export function callingSchedulePayload(value: CampaignCallingSchedule | null | undefined): CampaignCallingSchedule {
    return {
        timezone: hasText(value?.timezone) ? value.timezone : null,
        time_window_start: hasText(value?.time_window_start) ? value.time_window_start : null,
        time_window_end: hasText(value?.time_window_end) ? value.time_window_end : null,
        allowed_days: hasDays(value?.allowed_days) ? [...value.allowed_days] : null,
        ignore_schedule: Boolean(value?.ignore_schedule),
    };
}

/** Human labels of the schedule fields that are missing, in display order. */
export function missingScheduleFields(value: CampaignCallingSchedule | null | undefined): string[] {
    const missing: string[] = [];
    if (!hasText(value?.timezone)) missing.push("timezone");
    if (!hasText(value?.time_window_start)) missing.push("start time");
    if (!hasText(value?.time_window_end)) missing.push("end time");
    if (!hasDays(value?.allowed_days)) missing.push("days");
    return missing;
}

/** The Task 5 warning, or null when every field is set. */
export function scheduleMissingWarning(value: CampaignCallingSchedule | null | undefined): string | null {
    const missing = missingScheduleFields(value);
    if (missing.length === 0) return null;
    return `Scheduling information missing: ${missing.join(", ")}. Until it is set, the default applies: 09:00–19:00 UTC, Monday–Friday, unless your account has its own default.`;
}

export function formatScheduleDays(days: number[] | null | undefined): string {
    if (!hasDays(days)) return FIELD_NOT_SET_TEXT;
    return [...days]
        .filter((d) => Number.isInteger(d) && d >= 0 && d < DAY_LABELS.length)
        .sort((a, b) => a - b)
        .map((d) => DAY_LABELS[d])
        .join(", ") || FIELD_NOT_SET_TEXT;
}

/** "{timezone} · {start}–{end} · {days}" from the SAVED values only; a
 *  missing field reads "Not set". Returns null when nothing at all is saved
 *  so the caller can show SCHEDULE_NOT_SET_TEXT instead. */
export function formatCallingSchedule(value: CampaignCallingSchedule | null | undefined): string | null {
    if (missingScheduleFields(value).length === 4) return null;
    const tz = hasText(value?.timezone) ? value.timezone : FIELD_NOT_SET_TEXT;
    const start = hasText(value?.time_window_start) ? value.time_window_start : FIELD_NOT_SET_TEXT;
    const end = hasText(value?.time_window_end) ? value.time_window_end : FIELD_NOT_SET_TEXT;
    return `${tz} · ${start}–${end} · ${formatScheduleDays(value?.allowed_days)}`;
}
