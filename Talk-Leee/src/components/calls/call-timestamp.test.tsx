import { test } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { render, screen } from "@testing-library/react";
import { CallTimestamp, callTimestampLabel, formatCallDuration } from "@/components/calls/call-timestamp";

const ISO = "2026-09-07T19:29:31.000Z";

test("the row shows only a clock icon — no visible date or time text", () => {
    render(<CallTimestamp iso={ISO} durationSeconds={212} />);
    const trigger = screen.getByTestId("call-timestamp");
    assert.ok(trigger.querySelector("svg"), "clock icon missing");
    // The only text inside the cell is the screen-reader-only <time>.
    const visibleText = Array.from(trigger.childNodes)
        .filter((node) => !(node instanceof HTMLElement && node.classList.contains("sr-only")))
        .map((node) => node.textContent ?? "")
        .join("")
        .trim();
    assert.equal(visibleText, "");
    assert.equal(trigger.getAttribute("aria-label"), `Call time ${callTimestampLabel(ISO)}`);
});

test("duration formatting used by the hover card", () => {
    assert.equal(formatCallDuration(212), "3:32");
    assert.equal(formatCallDuration(5), "0:05");
    assert.equal(formatCallDuration(null), "—");
});
