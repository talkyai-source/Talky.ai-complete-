import assert from "node:assert/strict";
import { test } from "node:test";
import { newlyUnread } from "@/lib/support-unread";

test("the first snapshot never announces anything", () => {
    assert.deepEqual(newlyUnread(null, { count: 3, ticketIds: ["a", "b", "c"] }), []);
});

test("a rising count announces exactly the ids that were not unread before", () => {
    assert.deepEqual(newlyUnread({ count: 1, ticketIds: ["a"] }, { count: 3, ticketIds: ["a", "b", "c"] }), ["b", "c"]);
});

test("a flat or falling count announces nothing, even when the set changed", () => {
    assert.deepEqual(newlyUnread({ count: 2, ticketIds: ["a", "b"] }, { count: 2, ticketIds: ["a", "c"] }), []);
    assert.deepEqual(newlyUnread({ count: 2, ticketIds: ["a", "b"] }, { count: 1, ticketIds: ["c"] }), []);
});
