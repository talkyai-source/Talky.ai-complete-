import assert from "node:assert/strict";
import { test } from "node:test";

import { checkCallingWindow } from "@/lib/calling-window";

// Wednesday 2026-11-04 22:00 UTC (23:00 in Berlin, 22:00 in London).
const LATE_UTC = new Date("2026-11-04T22:00:00Z");
// Wednesday 2026-11-04 12:00 UTC.
const NOON_UTC = new Date("2026-11-04T12:00:00Z");

test("TZ4: a window with no saved hours is evaluated and shown as the UTC default, never as a local window", () => {
    const check = checkCallingWindow({ timezone: "Europe/Berlin" }, LATE_UTC);
    assert.equal(check.outside, true);
    assert.equal(check.tz, "UTC");
    assert.equal(check.message, "It's 10:00 PM in UTC, outside the default 9:00 AM–7:00 PM UTC calling window.");
    assert.equal(checkCallingWindow(null, NOON_UTC).outside, false);
    assert.equal(checkCallingWindow({ timezone: "Europe/Berlin" }, NOON_UTC).outside, false);
});

test("a fully saved window is evaluated in the campaign's own zone and shown without the default label", () => {
    const saved = { timezone: "Europe/Berlin", time_window_start: "09:00", time_window_end: "17:00", allowed_days: [0, 1, 2, 3, 4] };
    const check = checkCallingWindow(saved, LATE_UTC);
    assert.equal(check.outside, true);
    assert.equal(check.tz, "Europe/Berlin");
    assert.equal(check.message, "It's 11:00 PM in Europe/Berlin, outside the 9:00 AM–5:00 PM calling window.");
    assert.ok(!check.message.includes("default"));
    assert.equal(checkCallingWindow(saved, NOON_UTC).outside, false);
});
