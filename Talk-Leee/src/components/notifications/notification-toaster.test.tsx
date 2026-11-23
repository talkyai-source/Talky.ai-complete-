/**
 * The toast's title line wraps: a long title is shown in full, never cut with
 * an ellipsis. jsdom has no layout, so this checks the title is not put in the
 * "one line, cut" mode and keeps all its text; the measured proof at every
 * screen width is in tests/telephony-buy-now.spec.ts.
 */
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import React from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { notificationsStore } from "@/lib/notifications";
import { NotificationToaster } from "./notification-toaster";

beforeEach(() => { notificationsStore.setIdentity({ tenantId: "toaster", userId: "someone" }); });
afterEach(() => { cleanup(); notificationsStore.setIdentity(null); });

const LONG_TITLE = "Thank you. Your documents have been submitted. ".repeat(4).trim();

function show(title: string, message?: string) {
    render(<NotificationToaster />);
    act(() => { notificationsStore.create({ type: "success", title, message }); });
    return screen.getByRole("status", { name: "Success notification" });
}

test("a long toast title wraps and is never cut", () => {
    const toast = show(LONG_TITLE);
    const title = Array.from(toast.querySelectorAll("div")).find((el) => el.children.length === 0 && el.textContent === LONG_TITLE) as HTMLElement;
    assert.equal(title !== undefined, true, "the whole title is in the toast");
    const classes = Array.from(title.classList);
    // Not the one-line-with-ellipsis mode, in any of its spellings.
    for (const cut of ["truncate", "text-ellipsis", "whitespace-nowrap", "overflow-hidden", "line-clamp-1"]) {
        assert.equal(classes.includes(cut), false, cut);
    }
    assert.equal(classes.includes("break-words"), true, "long words break instead of overflowing");
    // The look of the title is unchanged.
    for (const kept of ["text-sm", "font-semibold", "text-foreground"]) assert.equal(classes.includes(kept), true, kept);
});

test("a toast with a title only has no second line; one with a message keeps it", () => {
    const only = show("Saved");
    const leaves = (toast: HTMLElement) => Array.from(toast.querySelectorAll("div")).filter((el) => el.children.length === 0 && (el.textContent ?? "") !== "").map((el) => el.textContent);
    assert.deepEqual(leaves(only), ["Saved"]);
    cleanup();
    notificationsStore.setIdentity({ tenantId: "toaster", userId: "someone-else" });
    const both = show("Save failed", "Please try again.");
    assert.deepEqual(leaves(both), ["Save failed", "Please try again."]);
});
