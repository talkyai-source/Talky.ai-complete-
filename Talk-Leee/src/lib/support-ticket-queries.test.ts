import assert from "node:assert/strict";
import { test } from "node:test";
import { TICKET_TABS, mergeNewestFirst } from "@/lib/support-ticket-queries";
import type { TicketListItem } from "@/lib/support-api";

const item = (id: string, updated: string, status: TicketListItem["status"] = "open"): TicketListItem => ({
    id, number: `SUP-00000${id}`, subject: id, status, priority: "normal", category: "bug", updated_at: updated, unread: false,
});

test("the Open tab covers both open and in_progress, the others one status each", () => {
    assert.deepEqual(TICKET_TABS.map((tab) => [tab.id, tab.statuses]), [
        ["open", ["open", "in_progress"]], ["waiting", ["waiting_on_customer"]], ["resolved", ["resolved"]], ["closed", ["closed"]],
    ]);
});

test("pages from several statuses merge newest first without duplicates", () => {
    const merged = mergeNewestFirst([
        [item("1", "2026-10-30T10:00:00Z"), item("2", "2026-10-29T10:00:00Z")],
        [item("3", "2026-10-30T09:00:00Z", "in_progress"), item("1", "2026-10-30T10:00:00Z")],
    ]);
    assert.deepEqual(merged.map((entry) => entry.id), ["1", "3", "2"]);
});
