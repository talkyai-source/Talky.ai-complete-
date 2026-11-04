import assert from "node:assert/strict";
import { test } from "node:test";

import {
    CALLING_HOURS_RULE_TEXT,
    FIELD_NOT_SET_TEXT,
    OUTSIDE_HOURS_TEXT,
    SCHEDULE_NOT_SET_TEXT,
    callingSchedulePayload,
    defaultCallingSchedule,
    formatCallingSchedule,
    formatScheduleDays,
    missingScheduleFields,
    scheduleMissingWarning,
} from "@/lib/calling-schedule-text";

const LONDON = {
    timezone: "Europe/London",
    time_window_start: "09:00",
    time_window_end: "17:00",
    allowed_days: [0, 1, 2, 3, 4],
    ignore_schedule: false,
};

test("TZ3/TZ4: the texts are exactly the agreed wording", () => {
    assert.equal(
        CALLING_HOURS_RULE_TEXT,
        "Calling hours are checked in each contact's own timezone when it is known; otherwise in this campaign's timezone.",
    );
    assert.equal(OUTSIDE_HOURS_TEXT, "Calling outside these hours depends on platform settings and is not guaranteed.");
    assert.equal(
        SCHEDULE_NOT_SET_TEXT,
        "Not set. The default of 09:00–19:00 UTC, Monday–Friday applies unless your account has its own default.",
    );
    assert.equal(FIELD_NOT_SET_TEXT, "Not set");
});

test("TZ1: a new campaign starts from the browser zone, 09:00–19:00, Monday–Friday — every value explicit", () => {
    const value = defaultCallingSchedule();
    assert.equal(value.timezone, Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
    assert.equal(value.time_window_start, "09:00");
    assert.equal(value.time_window_end, "19:00");
    assert.deepEqual(value.allowed_days, [0, 1, 2, 3, 4]);
    assert.equal(value.ignore_schedule, false);
});

test("TZ1: the payload always carries all four fields, null when unset", () => {
    assert.deepEqual(callingSchedulePayload(LONDON), LONDON);
    assert.deepEqual(callingSchedulePayload({}), {
        timezone: null, time_window_start: null, time_window_end: null, allowed_days: null, ignore_schedule: false,
    });
    assert.deepEqual(callingSchedulePayload(null), {
        timezone: null, time_window_start: null, time_window_end: null, allowed_days: null, ignore_schedule: false,
    });
    // Blank strings are "not set"; an empty day list is sent as null so the
    // backend never stores [] (which would allow no day at all).
    assert.deepEqual(callingSchedulePayload({ timezone: " ", time_window_start: "", allowed_days: [], ignore_schedule: true }), {
        timezone: null, time_window_start: null, time_window_end: null, allowed_days: null, ignore_schedule: true,
    });
    const keys = Object.keys(callingSchedulePayload({ timezone: "Europe/London" }));
    assert.deepEqual(keys.sort(), ["allowed_days", "ignore_schedule", "time_window_end", "time_window_start", "timezone"]);
});

test("TZ5: the missing-information warning names each missing field exactly", () => {
    assert.equal(scheduleMissingWarning(LONDON), null);
    assert.deepEqual(missingScheduleFields(null), ["timezone", "start time", "end time", "days"]);
    assert.equal(
        scheduleMissingWarning(null),
        "Scheduling information missing: timezone, start time, end time, days. Until it is set, the default applies: 09:00–19:00 UTC, Monday–Friday, unless your account has its own default.",
    );
    assert.equal(
        scheduleMissingWarning({ timezone: "Europe/London", allowed_days: [] }),
        "Scheduling information missing: start time, end time, days. Until it is set, the default applies: 09:00–19:00 UTC, Monday–Friday, unless your account has its own default.",
    );
});

test("TZ2: the detail line is built from the saved values only", () => {
    assert.equal(formatCallingSchedule(LONDON), "Europe/London · 09:00–17:00 · Mon, Tue, Wed, Thu, Fri");
    assert.equal(formatCallingSchedule(null), null);
    assert.equal(formatCallingSchedule({}), null);
    assert.equal(formatCallingSchedule({ timezone: null, time_window_start: null, time_window_end: null, allowed_days: null }), null);
    assert.equal(formatCallingSchedule({ timezone: "Europe/London" }), "Europe/London · Not set–Not set · Not set");
    assert.equal(formatScheduleDays([5, 6]), "Sat, Sun");
    assert.equal(formatScheduleDays([4, 0]), "Mon, Fri");
    assert.equal(formatScheduleDays([]), "Not set");
    assert.equal(formatScheduleDays(null), "Not set");
});
