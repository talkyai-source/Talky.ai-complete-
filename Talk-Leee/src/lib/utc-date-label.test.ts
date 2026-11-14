import assert from "node:assert/strict";
import { test } from "node:test";
import { browserTimeZoneName, formatUtcDateLabel, timezoneLabel, UTC_TIMEZONE_LABEL } from "./utc-date-label";

// Plan 3.7 Z3: a UTC day bucket prints its UTC date whatever the browser zone.

function withTimeZone(zone: string, run: () => void) {
    const before = process.env.TZ;
    process.env.TZ = zone;
    try {
        run();
    } finally {
        if (before === undefined) delete process.env.TZ;
        else process.env.TZ = before;
    }
}

test("a UTC day bucket prints its UTC date with the browser behind UTC", () => {
    withTimeZone("America/Los_Angeles", () => {
        // The local date of UTC midnight is the previous day here.
        assert.equal(new Date("2026-03-01").getDate(), 28);
        assert.equal(new Date("2026-03-01").toLocaleDateString("en-US", { month: "short", day: "numeric" }), "Feb 28");
        assert.equal(formatUtcDateLabel("2026-03-01"), "3/1/2026");
        assert.equal(formatUtcDateLabel(Date.parse("2026-03-01")), "3/1/2026");
        assert.equal(formatUtcDateLabel(new Date("2026-03-01T00:00:00Z")), "3/1/2026");
        assert.equal(browserTimeZoneName(), "America/Los_Angeles");
    });
});

test("a UTC day bucket prints its UTC date with the browser ahead of UTC", () => {
    withTimeZone("Asia/Tokyo", () => {
        // The local date of this UTC instant is the next day here.
        assert.equal(new Date("2026-03-01T23:30:00Z").getDate(), 2);
        assert.equal(formatUtcDateLabel("2026-03-01T23:30:00Z"), "3/1/2026");
        assert.equal(formatUtcDateLabel("2026-03-01"), "3/1/2026");
        assert.equal(formatUtcDateLabel("2026-03-01T10:00"), "3/1/2026");
        assert.equal(browserTimeZoneName(), "Asia/Tokyo");
    });
});

test("labels use the exact wording and an unreadable date prints nothing", () => {
    assert.equal(UTC_TIMEZONE_LABEL, "Timezone: UTC");
    assert.equal(timezoneLabel("Europe/London"), "Timezone: Europe/London");
    assert.equal(formatUtcDateLabel("not a date"), "");
});
