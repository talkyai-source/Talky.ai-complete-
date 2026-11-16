/**
 * 3.7 Z3: the timezone a date-sensitive figure really uses, and the date it
 * prints for a day bucket.
 *
 * The Dashboard reads its summary and its analytics (daily, hourly and
 * per-campaign) in the account's reporting zone, which the summary reports,
 * so its figures say "Timezone: {zone name}" with that zone (the browser's
 * until the summary has said which). A server day label ("2026-03-01") is
 * printed as that same calendar date, and a bucket instant as its date in the
 * reporting zone. The Analytics page still says "Timezone: UTC".
 */

export const UTC_TIMEZONE_LABEL = "Timezone: UTC";

/** The browser's IANA zone name, or "UTC" when it cannot be read. */
export function browserTimeZoneName(): string {
    try {
        return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    } catch {
        return "UTC";
    }
}

export function timezoneLabel(zone: string): string {
    return `Timezone: ${zone}`;
}

/**
 * The date ("3/1/2026" in en-US) for a UTC day bucket ("2026-03-01", an ISO timestamp, or its ms),
 * printed as the UTC date in every browser zone. Without the UTC option a
 * browser behind UTC printed the previous day for the same bucket.
 */
export function formatUtcDateLabel(value: string | number | Date): string {
    const date = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleDateString(undefined, { timeZone: "UTC" });
}

/**
 * The date of an instant (a bucket's start, in ms) in a named zone. A bucket
 * that starts at a zone's midnight is the previous UTC day in a zone ahead of
 * UTC, so it is printed in the zone it was cut in.
 */
export function formatZoneDateLabel(value: number | Date, zone: string): string {
    const date = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    try {
        return date.toLocaleDateString(undefined, { timeZone: zone });
    } catch {
        return "";
    }
}
