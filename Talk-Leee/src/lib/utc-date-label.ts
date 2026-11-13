/**
 * 3.7 Z3: the timezone a date-sensitive figure really uses, and the date it
 * prints for a UTC day bucket.
 *
 * The backend groups analytics days by UTC date and the Dashboard month range
 * is built in UTC, so those figures say "Timezone: UTC" and print the UTC
 * date. The Dashboard live chart (and its Custom range) uses the browser's
 * zone, so it says "Timezone: {zone name}".
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
