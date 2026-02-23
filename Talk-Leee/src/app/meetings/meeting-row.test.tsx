import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { cleanup } from "@testing-library/react";
import { ensureDom } from "@/test-utils/dom";
import { meetingParticipantSummary } from "@/lib/meetings-utils";
import type { CalendarEvent } from "@/lib/models";

ensureDom();

afterEach(() => cleanup());

test("MeetingRow shows participant summary when participants exist", () => {
    const meeting: CalendarEvent = {
        id: "m1",
        title: "Demo",
        startTime: "2026-01-14T13:00:00Z",
        participants: [
            { id: undefined, name: "Ada", email: undefined, role: undefined },
            { id: undefined, name: "Bob", email: undefined, role: undefined },
            { id: undefined, name: "Cara", email: undefined, role: undefined },
        ],
    };

    assert.equal(meetingParticipantSummary(meeting), "Ada, Bob +1");
});

test("MeetingRow omits participant summary when empty", () => {
    const meeting: CalendarEvent = { id: "m2", title: "Demo", startTime: "2026-01-14T13:00:00Z", participants: [] };
    assert.equal(meetingParticipantSummary(meeting), "");
});
