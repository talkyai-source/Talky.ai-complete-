/**
 * FX6c Task 0.1 (plan 2.3: "The hours on screen match what the backend
 * enforces.") and Task 0.2 (P7). When the server says the dialer ignores the
 * hours (effective_schedule.ignore_schedule true), the enforced hours line
 * still shows and the editor's own "Call anytime (ignore these hours)" label
 * sits directly under it; the Start window then shows no out-of-hours warning.
 * While the Start window reads the enforced hours its spinner carries the
 * app's hidden "Loading…" text.
 */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";

import type { CampaignEffectiveSchedule } from "@/lib/dashboard-api";
import { CALL_ANYTIME_LABEL, OUTSIDE_HOURS_TEXT } from "@/lib/calling-schedule-text";
import { CallingScheduleEditor } from "./calling-schedule-editor";
import { CallingScheduleLine, CallingScheduleStartNotice } from "./calling-schedule-notice";

afterEach(cleanup);

const LABEL = "Call anytime (ignore these hours)";
const SAVED = { timezone: "Asia/Karachi", time_window_start: "10:00", time_window_end: "22:00", allowed_days: [5, 6], ignore_schedule: true };
/** A day (0 = Mon) that is never today in UTC, so the enforced hours below are always "outside". */
const notToday = ((new Date().getUTCDay() + 6) % 7 + 3) % 7;
const effective = (over: Partial<CampaignEffectiveSchedule> = {}): CampaignEffectiveSchedule => ({
    timezone: "UTC", time_window_start: "08:00", time_window_end: "17:00", allowed_days: [notToday],
    ignore_schedule_requested: true, ignore_schedule_allowed: true, ignore_schedule: true, ...over,
});
const ENFORCED_LINE = /^Calling schedule: UTC · 08:00–17:00 · (Mon|Tue|Wed|Thu|Fri|Sat|Sun)$/;

test("the label is one shared constant and the editor renders it exactly as before", () => {
    assert.equal(CALL_ANYTIME_LABEL, LABEL);
    render(<CallingScheduleEditor value={{ ...SAVED, ignore_schedule: false }} onChange={() => {}} />);
    const label = screen.getByText(LABEL);
    assert.equal(label.tagName, "SPAN");
    assert.equal(label.className, "font-medium");
    assert.equal(label.parentElement?.textContent, `${LABEL}${OUTSIDE_HOURS_TEXT}`);
    assert.equal((label.closest("label")?.querySelector("input[type=checkbox]") as HTMLInputElement | null)?.checked, false);
});

test("header line: call anytime in force shows the enforced hours and the label directly under them", () => {
    render(<CallingScheduleLine callingConfig={SAVED} effectiveSchedule={effective()} testId="campaign-calling-schedule" />);
    const line = screen.getByTestId("campaign-calling-schedule");
    assert.match(line.textContent ?? "", ENFORCED_LINE);
    assert.equal(line.nextElementSibling?.textContent, LABEL, "the label is the element directly under the line");
    assert.equal(document.body.textContent?.includes("Asia/Karachi"), false, "the saved values are not shown");
});

test("header line: ignore_schedule false, or no effective_schedule, shows no label", () => {
    render(<CallingScheduleLine callingConfig={SAVED} effectiveSchedule={effective({ ignore_schedule_allowed: false, ignore_schedule: false })} />);
    assert.match(screen.getByTestId("calling-schedule-line").textContent ?? "", ENFORCED_LINE);
    assert.equal(screen.queryByText(LABEL) === null, true);
    cleanup();
    render(<CallingScheduleLine callingConfig={SAVED} effectiveSchedule={null} />);
    assert.equal(screen.getByTestId("calling-schedule-line").textContent, "Calling schedule: Asia/Karachi · 10:00–22:00 · Sat, Sun");
    assert.equal(screen.queryByText(LABEL) === null, true);
});

test("Start window: call anytime in force shows the enforced hours, the label under them, and no out-of-hours warning", () => {
    render(<CallingScheduleStartNotice callingConfig={SAVED} effectiveSchedule={effective()} />);
    const line = screen.getByTestId("calling-schedule-line");
    assert.match(line.textContent ?? "", ENFORCED_LINE);
    assert.equal(line.nextElementSibling?.textContent, LABEL);
    assert.equal(document.body.textContent?.includes(OUTSIDE_HOURS_TEXT), false, "no out-of-hours warning");
});

test("Start window: the same hours with ignore_schedule false still warn, exactly as now", () => {
    render(<CallingScheduleStartNotice callingConfig={SAVED} effectiveSchedule={effective({ ignore_schedule_allowed: false, ignore_schedule: false })} />);
    assert.equal(screen.queryByText(LABEL) === null, true);
    assert.equal(document.body.textContent?.includes("Today isn't in this campaign's allowed calling days (UTC)."), true);
    assert.equal(document.body.textContent?.includes(OUTSIDE_HOURS_TEXT), true);
});

test("Start window: while the enforced hours are read, the spinner carries the hidden Loading… text", () => {
    render(<CallingScheduleStartNotice callingConfig={SAVED} reading />);
    const status = screen.getByTestId("calling-schedule-reading");
    assert.equal(status.getAttribute("role"), "status");
    const hidden = status.querySelector(".sr-only");
    assert.equal(hidden !== null, true, "the spinner has a screen-reader text");
    assert.equal(hidden?.textContent, "Loading…");
    assert.equal(status.querySelector("svg")?.getAttribute("aria-hidden"), "true", "the spinner itself stays hidden from screen readers");
    assert.equal(status.textContent, "Loading…", "nothing else is added");
});
