import { test } from "node:test";
import assert from "node:assert/strict";
import {
    splitAndSortMeetings,
    meetingLeadLabel,
    meetingParticipantSummary,
    meetingStatusLabel,
    sortMeetings,
    sanitizeMeetingNotesHtml,
} from "@/lib/meetings-utils";
import type { CalendarEvent } from "@/lib/models";

test("splitAndSortMeetings splits by start time and sorts correctly", () => {
    const now = Date.parse("2026-01-14T12:00:00Z");
    const items: CalendarEvent[] = [
        { id: "a", title: "A", startTime: "2026-01-14T13:00:00Z" },
        { id: "b", title: "B", startTime: "2026-01-14T11:00:00Z" },
        { id: "c", title: "C", startTime: "2026-01-14T14:00:00Z" },
        { id: "d", title: "D", startTime: "2026-01-14T10:00:00Z" },
    ];

    const { upcoming, past } = splitAndSortMeetings(items, now);
    assert.deepEqual(upcoming.map((m) => m.id), ["a", "c"]);
    assert.deepEqual(past.map((m) => m.id), ["b", "d"]);
});

test("meetingLeadLabel prefers explicit leadName, else participant name/email", () => {
    const a: CalendarEvent = { id: "a", title: "A", startTime: "2026-01-14T13:00:00Z", leadName: "Ada" };
    const b: CalendarEvent = {
        id: "b",
        title: "B",
        startTime: "2026-01-14T13:00:00Z",
        participants: [{ id: undefined, name: "Bob", email: undefined, role: undefined }],
    };
    const c: CalendarEvent = {
        id: "c",
        title: "C",
        startTime: "2026-01-14T13:00:00Z",
        participants: [{ id: undefined, name: undefined, email: "c@example.test", role: undefined }],
    };
    const d: CalendarEvent = { id: "d", title: "D", startTime: "2026-01-14T13:00:00Z" };

    assert.equal(meetingLeadLabel(a), "Ada");
    assert.equal(meetingLeadLabel(b), "Bob");
    assert.equal(meetingLeadLabel(c), "c@example.test");
    assert.equal(meetingLeadLabel(d), "—");
});

test("meetingStatusLabel normalizes status values", () => {
    const a: CalendarEvent = { id: "a", title: "A", startTime: "2026-01-14T13:00:00Z", status: "cancelled" };
    const b: CalendarEvent = { id: "b", title: "B", startTime: "2026-01-14T13:00:00Z", status: "completed" };
    const c: CalendarEvent = { id: "c", title: "C", startTime: "2026-01-14T13:00:00Z", status: "scheduled" };
    const d: CalendarEvent = { id: "d", title: "D", startTime: "2026-01-14T13:00:00Z", status: "weird" };

    assert.equal(meetingStatusLabel(a), "Cancelled");
    assert.equal(meetingStatusLabel(b), "Completed");
    assert.equal(meetingStatusLabel(c), "Scheduled");
    assert.equal(meetingStatusLabel(d), "weird");
});

test("meetingParticipantSummary returns first two and extra count", () => {
    const a: CalendarEvent = {
        id: "a",
        title: "A",
        startTime: "2026-01-14T13:00:00Z",
        participants: [
            { id: undefined, name: "Ada", email: undefined, role: undefined },
            { id: undefined, name: "Bob", email: undefined, role: undefined },
            { id: undefined, name: "Cara", email: undefined, role: undefined },
        ],
    };
    const b: CalendarEvent = { id: "b", title: "B", startTime: "2026-01-14T13:00:00Z", participants: [] };

    assert.equal(meetingParticipantSummary(a), "Ada, Bob +1");
    assert.equal(meetingParticipantSummary(b), "");
});

test("sortMeetings can sort by title and startTime", () => {
    const items: CalendarEvent[] = [
        { id: "b", title: "Bravo", startTime: "2026-01-14T14:00:00Z" },
        { id: "a", title: "alpha", startTime: "2026-01-14T13:00:00Z" },
        { id: "c", title: "Charlie", startTime: "not-a-date" },
    ];

    assert.deepEqual(
        sortMeetings(items, "title", "asc").map((m) => m.id),
        ["a", "b", "c"]
    );
    assert.deepEqual(
        sortMeetings(items, "startTime", "asc").map((m) => m.id),
        ["a", "b", "c"]
    );
    assert.deepEqual(
        sortMeetings(items, "startTime", "desc").map((m) => m.id),
        ["c", "b", "a"]
    );
});

test("sanitizeMeetingNotesHtml removes scripts and event handlers", () => {
    const input = `<div onclick="alert(1)"><b>Hi</b><script>alert(1)</script><img src=x onerror="alert(2)"></div>`;
    const out = sanitizeMeetingNotesHtml(input);
    assert.ok(!out.toLowerCase().includes("script"));
    assert.ok(!out.toLowerCase().includes("onclick"));
    assert.ok(!out.toLowerCase().includes("onerror"));
    assert.ok(out.includes("<b>Hi</b>"));
});
