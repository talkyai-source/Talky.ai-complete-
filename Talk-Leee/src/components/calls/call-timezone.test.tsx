import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { CallTimestamp, callTimestampLabel } from "./call-timestamp";
import { formatSessionTime } from "@/lib/session-utils";

afterEach(cleanup);

for (const [at, offset] of [
    ["2026-11-01T05:30:00Z", "GMT-4"],
    ["2026-11-01T06:30:00Z", "GMT-5"],
]) {
    test(`call timestamp identifies the repeated DST hour at ${at}`, () => {
        const label = callTimestampLabel(at, "America/New_York");
        assert.ok(label.includes(offset), `Expected explicit ${offset}: ${label}`);
        render(<CallTimestamp iso={at} timeZone="America/New_York" />);
        assert.ok(screen.getByTestId("call-timestamp-text").textContent?.includes(offset));
        assert.ok(screen.getByTestId("call-timestamp").getAttribute("title")?.includes(new Date(at).toISOString()),
            "The time must expose its unambiguous UTC instant on hover");
    });
}

for (const [seconds, expected] of [[61, "1 minute ago"], [121, "2 minutes ago"],
    [3601, "1 hour ago"], [7201, "2 hours ago"], [86401, "1 day ago"], [172801, "2 days ago"]] as const) {
    test(`session age pluralizes ${expected}`, (context) => {
        const now = new Date("2026-11-06T12:00:00Z");
        context.mock.timers.enable({ apis: ["Date"], now });
        assert.equal(formatSessionTime(new Date(now.getTime() - seconds * 1000).toISOString()), expected);
    });
}

test("call details, mobile history and MFA verification use the shared zone formatter", () => {
    for (const file of ["src/app/calls/page.tsx", "src/app/calls/[id]/page.tsx", "src/app/security/page.tsx"]) {
        const source = readFileSync(file, "utf8");
        assert.match(source, /formatTimestamp/, `${file} must use the shared zone formatter`);
        assert.doesNotMatch(source, /new Date\((?:call\.created_at|mfaVerifiedAt)\)\.toLocaleString\(\)/);
    }
});
