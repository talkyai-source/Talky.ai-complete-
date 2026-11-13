import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { LiveCallsTimeSeriesChart } from "./dashboard-charts";

const buckets = [
    { startMs: Date.parse("2026-10-31T19:00:00Z"), endMs: Date.parse("2026-11-01T19:00:00Z"), total: 2, answered: 2, failed: 0, avgDurationSec: 60 },
    { startMs: Date.parse("2026-11-01T19:00:00Z"), endMs: Date.parse("2026-11-02T19:00:00Z"), total: 3, answered: 3, failed: 0, avgDurationSec: 60 },
];
beforeEach(() => {
    const original = Date.prototype.toLocaleTimeString;
    mock.method(Date.prototype, "toLocaleTimeString", function (this: Date, _locale?: Intl.LocalesArgument, options?: Intl.DateTimeFormatOptions) {
        return original.call(this, "en-GB", { timeZone: "America/Los_Angeles", ...options, hourCycle: "h23" });
    });
});
afterEach(() => { cleanup(); mock.restoreAll(); });

test("live chart ticks use the supplied reporting zone instead of the browser zone", () => {
    const props = { buckets, timeZone: "Asia/Karachi" };
    render(<LiveCallsTimeSeriesChart {...props} />);
    assert.equal(screen.getAllByText("00:00").length, 2);
    assert.equal(screen.queryAllByText("12:00").length, 0);
});

test("live chart callers without a reporting zone retain the browser-local default", () => {
    render(<LiveCallsTimeSeriesChart buckets={buckets} />);
    assert.equal(screen.getAllByText("12:00").length, 1);
    assert.equal(screen.getAllByText("11:00").length, 1);
});
