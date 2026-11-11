import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";

import { CallingScheduleLine, CallingScheduleStartNotice } from "@/components/campaigns/calling-schedule-notice";

afterEach(cleanup);

const NOT_SET = "Not set. The default of 09:00–19:00 UTC, Monday–Friday applies unless your account has its own default.";
const RULE = "Calling hours are checked in each contact's own timezone when it is known; otherwise in this campaign's timezone.";

test("TZ2: the detail page line shows the saved schedule", () => {
    render(<CallingScheduleStartNotice callingConfig={{
        timezone: "Europe/London", time_window_start: "09:00", time_window_end: "17:00", allowed_days: [0, 1, 2, 3, 4], ignore_schedule: false,
    }} />);
    assert.equal(
        screen.getByTestId("calling-schedule-line").textContent,
        "Calling schedule: Europe/London · 09:00–17:00 · Mon, Tue, Wed, Thu, Fri",
    );
    assert.ok(screen.getByText(RULE));
    assert.ok(!screen.queryByTestId("calling-schedule-missing"));
});

test("TZ4/TZ5: a campaign with no saved schedule shows the default text and the missing warning", () => {
    render(<CallingScheduleStartNotice callingConfig={null} />);
    assert.equal(screen.getByTestId("calling-schedule-line").textContent, NOT_SET);
    assert.equal(
        screen.getByTestId("calling-schedule-missing").textContent,
        "Scheduling information missing: timezone, start time, end time, days. Until it is set, the default applies: 09:00–19:00 UTC, Monday–Friday, unless your account has its own default.",
    );
    assert.ok(!document.body.textContent?.includes("Calling schedule:"));
});

test("TZ5: a partly saved schedule keeps the saved parts, marks the rest Not set, and warns", () => {
    render(<CallingScheduleStartNotice callingConfig={{ timezone: "Europe/London", allowed_days: [0, 2, 4] }} />);
    assert.equal(screen.getByTestId("calling-schedule-line").textContent, "Calling schedule: Europe/London · Not set–Not set · Mon, Wed, Fri");
    assert.equal(
        screen.getByTestId("calling-schedule-missing").textContent,
        "Scheduling information missing: start time, end time. Until it is set, the default applies: 09:00–19:00 UTC, Monday–Friday, unless your account has its own default.",
    );
});

test("TZ2/TZ5: the campaign detail page mounts the notice from calling_config inside the Start dialog, above the Start action", () => {
    // The Start dialog is now the shared one (plan 3.2): the page opens it with
    // its campaign, and the notice sits inside that dialog's Modal.
    const page = readFileSync(path.join(process.cwd(), "src/app/campaigns/[id]/page.tsx"), "utf8");
    const shared = readFileSync(path.join(process.cwd(), "src/components/campaigns/campaign-start-dialog.tsx"), "utf8");
    const mount = shared.indexOf("<CallingScheduleStartNotice callingConfig={dialog.targets[0]?.calling_config} />");
    const dialog = shared.indexOf("open={dialog.isOpen}");
    const dialogEnd = shared.indexOf("</Modal>", dialog);
    assert.notEqual(mount, -1);
    assert.ok(dialog !== -1 && mount > dialog && mount < dialogEnd, "notice must sit inside the Start dialog");
    assert.ok(page.includes("startDialog.open([campaign]);") && page.includes("<CampaignStartDialog dialog={startDialog} />"), "the detail page opens that dialog with its campaign");
    assert.ok(!page.includes("checkCallingWindow("), "the page no longer formats the window itself");
});

test("TZ2: the campaign header shows the saved schedule line without opening Start", () => {
    render(<CallingScheduleLine callingConfig={{
        timezone: "Europe/London", time_window_start: "09:00", time_window_end: "17:00", allowed_days: [0, 1, 2, 3, 4], ignore_schedule: false,
    }} testId="campaign-calling-schedule" />);
    assert.equal(
        screen.getByTestId("campaign-calling-schedule").textContent,
        "Calling schedule: Europe/London · 09:00–17:00 · Mon, Tue, Wed, Thu, Fri",
    );
    cleanup();
    render(<CallingScheduleLine callingConfig={null} testId="campaign-calling-schedule" />);
    assert.equal(screen.getByTestId("campaign-calling-schedule").textContent, NOT_SET);

    const page = readFileSync(path.join(process.cwd(), "src/app/campaigns/[id]/page.tsx"), "utf8");
    const header = page.search(/<CallingScheduleLine\s+callingConfig=\{campaign\.calling_config\}\s+testId="campaign-calling-schedule"/);
    const description = page.indexOf("{campaign.description}");
    // The Start dialog is now the shared component the page mounts (plan 3.2).
    const dialog = page.indexOf("<CampaignStartDialog dialog={startDialog} />");
    const shared = readFileSync(path.join(process.cwd(), "src/components/campaigns/campaign-start-dialog.tsx"), "utf8");
    assert.notEqual(header, -1, "header line must be mounted from calling_config");
    assert.ok(description !== -1 && header > description, "the line sits in the campaign information area, after the description");
    assert.ok(dialog !== -1 && header < dialog, "the line is outside the Start dialog");
    assert.ok(shared.includes("<CallingScheduleStartNotice callingConfig={dialog.targets[0]?.calling_config} />"), "the Start dialog still carries the notice");
});
