/** Calendar labels from the server's effective IANA zone, never the browser default. */
export function reportingDate(timeZone: string, now = new Date()): string {
    if (!timeZone) throw new Error("Reporting timezone unavailable");
    const parts = new Intl.DateTimeFormat("en-CA", {
        timeZone, year: "numeric", month: "2-digit", day: "2-digit",
    }).formatToParts(now);
    const part = (name: string) => parts.find((value) => value.type === name)!.value;
    return `${part("year")}-${part("month")}-${part("day")}`;
}

export function reportingDateRange(timeZone: string, days: number | "month", now = new Date()) {
    const to = reportingDate(timeZone, now);
    if (days === "month") return { from: `${to.slice(0, 7)}-01`, to };
    // Arithmetic is on a date label, not elapsed 24-hour periods in a DST zone.
    const from = new Date(`${to}T00:00:00Z`);
    from.setUTCDate(from.getUTCDate() - days);
    return { from: from.toISOString().slice(0, 10), to };
}

export function formatCalendarDate(value: string): string {
    return new Date(`${value.slice(0, 10)}T00:00:00Z`).toLocaleDateString("en-US", {
        timeZone: "UTC", month: "short", day: "numeric",
    });
}

function calendarMidnightMs(day: string, timeZone: string): number {
    const target = Date.parse(`${day}T00:00:00Z`);
    if (!Number.isFinite(target)) throw new Error("Invalid reporting calendar date");
    const formatter = new Intl.DateTimeFormat("en-GB", {
        timeZone, year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
    });
    let guess = target;
    let previous: number | undefined;
    for (let attempt = 0; attempt < 4; attempt++) {
        const parts = formatter.formatToParts(guess);
        const value = (name: string) => Number(parts.find((part) => part.type === name)!.value);
        const wallTime = Date.UTC(value("year"), value("month") - 1, value("day"), value("hour"), value("minute"), value("second"));
        const next = guess + target - wallTime;
        if (next === guess) return guess;
        // A midnight skipped by a forward transition starts at the first
        // valid instant after the gap, rather than the prior calendar day.
        if (next === previous) return Math.max(next, guess);
        previous = guess;
        guess = next;
    }
    throw new Error("Unable to resolve reporting calendar boundary");
}

export function calendarBucketBounds(day: string, timeZone: string) {
    const next = new Date(`${day}T00:00:00Z`);
    next.setUTCDate(next.getUTCDate() + 1);
    return {
        startMs: calendarMidnightMs(day, timeZone),
        endMs: calendarMidnightMs(next.toISOString().slice(0, 10), timeZone),
    };
}
