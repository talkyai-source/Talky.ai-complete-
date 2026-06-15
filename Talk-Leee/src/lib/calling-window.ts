// Client-side "is it a good time to call right now?" check (Phase 3c-v2).
//
// Mirrors the backend window logic enough to WARN the user — it never
// blocks. Evaluates the campaign's window in the campaign's timezone using
// Intl (no extra deps). Falls back to sensible defaults for any field the
// campaign left unset, matching the backend's tenant-default overlay.

import type { CampaignCallingSchedule } from "@/lib/dashboard-api";

const DEFAULT_TZ = "UTC";
const DEFAULT_START = "09:00";
const DEFAULT_END = "19:00";
const DEFAULT_DAYS = [0, 1, 2, 3, 4]; // Mon–Fri

const _WD: Record<string, number> = { Mon: 0, Tue: 1, Wed: 2, Thu: 3, Fri: 4, Sat: 5, Sun: 6 };

function partsInZone(tz: string, d = new Date()): { weekday: number; minutes: number } {
    const f = new Intl.DateTimeFormat("en-US", {
        timeZone: tz, weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false,
    });
    const parts = f.formatToParts(d);
    const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
    const weekday = _WD[get("weekday")] ?? 0;
    let hour = parseInt(get("hour"), 10);
    if (Number.isNaN(hour) || hour === 24) hour = 0;
    const minute = parseInt(get("minute"), 10) || 0;
    return { weekday, minutes: hour * 60 + minute };
}

function toMin(hhmm: string): number {
    const [h, m] = hhmm.split(":").map(Number);
    return (h || 0) * 60 + (m || 0);
}

function fmt12(minutes: number): string {
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
    const am = h < 12;
    const dh = h % 12 === 0 ? 12 : h % 12;
    return `${dh}:${String(m).padStart(2, "0")} ${am ? "AM" : "PM"}`;
}

export interface WindowCheck {
    outside: boolean;
    message: string;
    tz: string;
}

/** Is the current moment outside the campaign's calling window? Advisory only. */
export function checkCallingWindow(s?: CampaignCallingSchedule | null): WindowCheck {
    const tz = s?.timezone || DEFAULT_TZ;
    const start = s?.time_window_start || DEFAULT_START;
    const end = s?.time_window_end || DEFAULT_END;
    const days = s?.allowed_days && s.allowed_days.length ? s.allowed_days : DEFAULT_DAYS;

    let cur: { weekday: number; minutes: number };
    try {
        cur = partsInZone(tz);
    } catch {
        return { outside: false, message: "", tz };
    }

    const dayOk = days.includes(cur.weekday);
    const timeOk = cur.minutes >= toMin(start) && cur.minutes <= toMin(end);
    if (dayOk && timeOk) return { outside: false, message: "", tz };
    if (!dayOk) {
        return { outside: true, message: `Today isn't in this campaign's allowed calling days (${tz}).`, tz };
    }
    return {
        outside: true,
        message: `It's ${fmt12(cur.minutes)} in ${tz}, outside the ${fmt12(toMin(start))}–${fmt12(toMin(end))} calling window.`,
        tz,
    };
}
