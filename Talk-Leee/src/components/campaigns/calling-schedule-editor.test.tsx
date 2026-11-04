import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { CallingScheduleEditor } from "@/components/campaigns/calling-schedule-editor";
import type { CampaignCallingSchedule } from "@/lib/dashboard-api";

afterEach(cleanup);

const BROWSER_TZ = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
const RULE = "Calling hours are checked in each contact's own timezone when it is known; otherwise in this campaign's timezone.";
const OUTSIDE = "Calling outside these hours depends on platform settings and is not guaranteed.";
const LONDON: CampaignCallingSchedule = {
    timezone: "Europe/London", time_window_start: "09:00", time_window_end: "17:00", allowed_days: [0, 1, 2, 3, 4], ignore_schedule: false,
};

function source(relative: string): string {
    return readFileSync(path.join(process.cwd(), "src", relative), "utf8");
}

test("TZ2/TZ4: a campaign with nothing saved shows Not set, never the browser zone or 09:00/19:00", () => {
    render(<CallingScheduleEditor value={{}} onChange={() => {}} />);
    const tz = screen.getByRole("combobox", { name: "Calling timezone" });
    assert.equal(tz.textContent?.trim(), "Not set");
    assert.ok(!tz.textContent?.includes(BROWSER_TZ));
    assert.equal((screen.getByLabelText("Calling start time") as HTMLInputElement).value, "");
    assert.equal((screen.getByLabelText("Calling end time") as HTMLInputElement).value, "");
    assert.equal(screen.getByTestId("calling-start-not-set").textContent, "Not set");
    assert.equal(screen.getByTestId("calling-end-not-set").textContent, "Not set");
    assert.equal(screen.getByTestId("calling-days-not-set").textContent, "Not set");
    for (const day of ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]) {
        assert.equal(screen.getByRole("button", { name: day }).getAttribute("aria-pressed"), "false");
    }
    // 09:00/19:00 may only appear inside the default-applies sentence, never as a value.
    const outsideDefaultText = (document.body.textContent ?? "").replace(/09:00–19:00 UTC/g, "");
    assert.ok(!outsideDefaultText.includes("09:00"));
    assert.ok(!outsideDefaultText.includes("19:00"));
});

test("TZ2: explicit null fields are shown as Not set too", () => {
    render(<CallingScheduleEditor value={{ timezone: null, time_window_start: null, time_window_end: null, allowed_days: null }} onChange={() => {}} />);
    assert.equal(screen.getByRole("combobox", { name: "Calling timezone" }).textContent?.trim(), "Not set");
    assert.equal(screen.getAllByText("Not set").length, 4);
});

test("TZ2: saved values are shown exactly as saved", () => {
    render(<CallingScheduleEditor value={LONDON} onChange={() => {}} />);
    assert.equal(screen.getByRole("combobox", { name: "Calling timezone" }).textContent?.trim(), "UK (London)");
    assert.equal((screen.getByLabelText("Calling start time") as HTMLInputElement).value, "09:00");
    assert.equal((screen.getByLabelText("Calling end time") as HTMLInputElement).value, "17:00");
    for (const day of ["Mon", "Tue", "Wed", "Thu", "Fri"]) {
        assert.equal(screen.getByRole("button", { name: day }).getAttribute("aria-pressed"), "true");
    }
    for (const day of ["Sat", "Sun"]) {
        assert.equal(screen.getByRole("button", { name: day }).getAttribute("aria-pressed"), "false");
    }
    assert.ok(!screen.queryByTestId("calling-schedule-missing"));
    assert.equal(screen.queryAllByText("Not set").length, 0);
});

test("TZ2: a saved zone outside the short list is still shown as saved, not replaced", () => {
    render(<CallingScheduleEditor value={{ ...LONDON, timezone: "Australia/Sydney" }} onChange={() => {}} />);
    assert.equal(screen.getByRole("combobox", { name: "Calling timezone" }).textContent?.trim(), "Australia/Sydney");
});

test("TZ1: changing a field reports the whole schedule, including the untouched fields", () => {
    let next: CampaignCallingSchedule | null = null;
    render(<CallingScheduleEditor value={LONDON} onChange={(value) => { next = value; }} />);
    fireEvent.click(screen.getByRole("button", { name: "Sat" }));
    assert.deepEqual(next, { ...LONDON, allowed_days: [0, 1, 2, 3, 4, 5] });
    fireEvent.change(screen.getByLabelText("Calling end time"), { target: { value: "18:30" } });
    assert.deepEqual(next, { ...LONDON, time_window_end: "18:30" });
});

test("TZ3: the editor states the enforced rule and never promises calls outside the window", () => {
    render(<CallingScheduleEditor value={LONDON} onChange={() => {}} />);
    assert.ok(screen.getByText(RULE));
    assert.ok(screen.getByText(OUTSIDE));
    const forbidden = /will call regardless|still go out|dial now|dial immediately|will go out now|wait until the window opens|still see warnings/i;
    assert.ok(!forbidden.test(document.body.textContent ?? ""));
    for (const file of ["components/campaigns/calling-schedule-editor.tsx", "components/campaigns/calling-schedule-notice.tsx", "app/campaigns/[id]/page.tsx"]) {
        assert.ok(!forbidden.test(source(file)), `${file} still carries the old promise`);
    }
});

test("TZ5: a missing field raises the warning naming exactly the missing fields", () => {
    render(<CallingScheduleEditor value={{ timezone: "Europe/London", time_window_start: "09:00" }} onChange={() => {}} />);
    assert.equal(
        screen.getByTestId("calling-schedule-missing").textContent,
        "Scheduling information missing: end time, days. Until it is set, the default applies: 09:00–19:00 UTC, Monday–Friday, unless your account has its own default.",
    );
});

test("TZ5: the warning lists all four fields when nothing is saved and disappears once complete", () => {
    const { rerender } = render(<CallingScheduleEditor value={{}} onChange={() => {}} />);
    assert.equal(
        screen.getByTestId("calling-schedule-missing").textContent,
        "Scheduling information missing: timezone, start time, end time, days. Until it is set, the default applies: 09:00–19:00 UTC, Monday–Friday, unless your account has its own default.",
    );
    rerender(<CallingScheduleEditor value={LONDON} onChange={() => {}} />);
    assert.ok(!screen.queryByTestId("calling-schedule-missing"));
});
