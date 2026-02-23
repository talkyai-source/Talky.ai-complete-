import { test } from "node:test";
import assert from "node:assert/strict";
import { groupReminders, sanitizeFailureReason, sortReminders, retryGuidance } from "@/lib/reminders-utils";
import type { Reminder } from "@/lib/models";

function rem(partial: Partial<Reminder> & Pick<Reminder, "id" | "content" | "status" | "channel" | "scheduledAt">): Reminder {
    return {
        meetingId: undefined,
        meetingTitle: undefined,
        contactId: undefined,
        contactName: undefined,
        toEmail: undefined,
        toPhone: undefined,
        sentAt: undefined,
        failedAt: undefined,
        canceledAt: undefined,
        retryCount: undefined,
        maxRetries: undefined,
        nextRetryAt: undefined,
        failureReason: undefined,
        createdAt: undefined,
        updatedAt: undefined,
        ...partial,
    };
}

test("sanitizeFailureReason strips dangerous characters and truncates", () => {
    const raw = "<script>alert(1)</script>\r\n  token=abc123  ";
    const safe = sanitizeFailureReason(raw);
    assert.ok(!safe.includes("<script>"));
    assert.ok(safe.includes("‹script›"));
    assert.ok(!safe.includes("\r"));
});

test("groupReminders groups by meeting id then contact id", () => {
    const items: Reminder[] = [
        rem({ id: "1", content: "a", status: "scheduled", channel: "email", scheduledAt: "2026-01-01T10:00:00Z", meetingId: "m1", meetingTitle: "M1" }),
        rem({ id: "2", content: "b", status: "scheduled", channel: "email", scheduledAt: "2026-01-01T11:00:00Z", meetingId: "m1", meetingTitle: "M1" }),
        rem({ id: "3", content: "c", status: "scheduled", channel: "email", scheduledAt: "2026-01-01T12:00:00Z", contactId: "c1", contactName: "C1" }),
    ];
    const groups = groupReminders(items);
    assert.equal(groups.length, 2);
    assert.equal(groups.find((g) => g.key === "meeting:m1")?.items.length, 2);
    assert.equal(groups.find((g) => g.key === "contact:c1")?.items.length, 1);
});

test("sortReminders sorts by scheduledAt asc/desc", () => {
    const items: Reminder[] = [
        rem({ id: "1", content: "a", status: "scheduled", channel: "email", scheduledAt: "2026-01-01T12:00:00Z" }),
        rem({ id: "2", content: "b", status: "scheduled", channel: "email", scheduledAt: "2026-01-01T10:00:00Z" }),
    ];
    const asc = sortReminders(items, "scheduledAt", "asc").map((x) => x.id);
    const desc = sortReminders(items, "scheduledAt", "desc").map((x) => x.id);
    assert.deepEqual(asc, ["2", "1"]);
    assert.deepEqual(desc, ["1", "2"]);
});

test("retryGuidance returns actionable strings for failed reminders", () => {
    const none = retryGuidance(rem({ id: "1", content: "a", status: "scheduled", channel: "email", scheduledAt: "2026-01-01T10:00:00Z" }));
    assert.equal(none, "");

    const pending = retryGuidance(
        rem({ id: "2", content: "b", status: "failed", channel: "email", scheduledAt: "2026-01-01T10:00:00Z", retryCount: 1 })
    );
    assert.ok(pending.length > 0);

    const next = retryGuidance(
        rem({
            id: "3",
            content: "c",
            status: "failed",
            channel: "email",
            scheduledAt: "2026-01-01T10:00:00Z",
            retryCount: 1,
            nextRetryAt: "2026-01-01T10:05:00Z",
        })
    );
    assert.match(next, /Next retry/i);

    const exhausted = retryGuidance(
        rem({
            id: "4",
            content: "d",
            status: "failed",
            channel: "email",
            scheduledAt: "2026-01-01T10:00:00Z",
            retryCount: 3,
            maxRetries: 3,
        })
    );
    assert.match(exhausted, /No retries remaining/i);
});

