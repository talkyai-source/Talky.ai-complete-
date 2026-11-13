/** Explicit display zone plus an unambiguous UTC hover value. */
export function formatTimestamp(iso: string, timeZone?: string) {
    const date = new Date(iso);
    if (!Number.isFinite(date.getTime())) {
        return { label: "Unavailable time", date: "—", time: "—", offset: "", zone: "", utc: "Invalid timestamp" };
    }
    let zone = timeZone || Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    try { new Intl.DateTimeFormat(undefined, { timeZone: zone }); } catch { zone = "UTC"; }
    const options: Intl.DateTimeFormatOptions = {
        timeZone: zone, year: "numeric", month: "short", day: "numeric",
        hour: "numeric", minute: "2-digit", second: "2-digit", timeZoneName: "shortOffset",
    };
    const formatter = new Intl.DateTimeFormat(undefined, options);
    return {
        label: formatter.format(date),
        date: date.toLocaleDateString(undefined, { timeZone: zone, month: "numeric", day: "numeric" }),
        time: date.toLocaleTimeString(undefined, { timeZone: zone, hour: "numeric", minute: "2-digit" }),
        offset: formatter.formatToParts(date).find(part => part.type === "timeZoneName")?.value ?? zone,
        zone,
        utc: `${date.toISOString()} (UTC)`,
    };
}
