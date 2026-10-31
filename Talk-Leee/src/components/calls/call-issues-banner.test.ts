import { test } from "node:test";
import assert from "node:assert/strict";
import { issueAge } from "@/components/calls/call-issues-banner";
import { RECENT_CALL_ISSUE_WINDOW_MS } from "@/lib/extended-api";

const NOW = Date.parse("2026-09-10T12:00:00Z");

test("a stale critical failure is labelled with its age instead of reading as live", () => {
    assert.equal(issueAge("2026-08-20T13:25:43Z", NOW), "21 days ago");
    assert.equal(issueAge("2026-09-10T09:00:00Z", NOW), "3 h ago");
    assert.equal(issueAge("2026-09-10T11:58:00Z", NOW), "2 min ago");
    assert.equal(issueAge("2026-09-10T12:00:00Z", NOW), "just now");
    assert.equal(issueAge(null, NOW), null);
    assert.equal(issueAge("not a date", NOW), null);
});

test("the banner only asks the server for the last day of critical call issues", () => {
    assert.equal(RECENT_CALL_ISSUE_WINDOW_MS, 24 * 60 * 60 * 1000);
});
