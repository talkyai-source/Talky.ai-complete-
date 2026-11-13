/**
 * FX1a Task 4.1 — shared Modal: closing returns focus to the control that
 * opened it, and one Escape closes only the top dialog of a stack.
 */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React, { useState } from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

import { Modal } from "@/components/ui/modal";

afterEach(() => { cleanup(); });

const settle = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)); });
const dialogOpen = (name: string) => screen.queryByRole("dialog", { name }) !== null;
const focusedId = () => (document.activeElement as HTMLElement | null)?.id ?? "";

function Stack() {
    const [details, setDetails] = useState(false);
    const [confirm, setConfirm] = useState(false);
    return (
        <>
            <button id="open-details" type="button" onClick={() => setDetails(true)}>Open details</button>
            <Modal open={details} onOpenChange={setDetails} title="Details">
                <button id="open-confirm" type="button" onClick={() => setConfirm(true)}>Open confirm</button>
            </Modal>
            <Modal open={confirm} onOpenChange={setConfirm} title="Confirm">
                <button id="confirm-ok" type="button">OK</button>
            </Modal>
        </>
    );
}

function open(id: string) {
    const button = document.getElementById(id) as HTMLButtonElement;
    button.focus();
    fireEvent.click(button);
}

test("closing a dialog returns focus to the control that opened it", async () => {
    render(<Stack />);
    open("open-details");
    await settle();
    assert.equal(dialogOpen("Details"), true);
    assert.equal(focusedId() === "open-details", false, "focus moved into the dialog");
    fireEvent.keyDown(window, { key: "Escape" });
    await settle();
    assert.equal(dialogOpen("Details"), false);
    assert.equal(focusedId(), "open-details");
});

test("one Escape closes only the top dialog; the next Escape closes the one under it", async () => {
    render(<Stack />);
    open("open-details");
    await settle();
    open("open-confirm");
    await settle();
    assert.equal(dialogOpen("Details"), true);
    assert.equal(dialogOpen("Confirm"), true);

    fireEvent.keyDown(window, { key: "Escape" });
    await settle();
    assert.equal(dialogOpen("Confirm"), false, "the top dialog closed");
    assert.equal(dialogOpen("Details"), true, "the dialog under it stayed open");
    assert.equal(focusedId(), "open-confirm", "focus returned to the control that opened the top dialog");

    fireEvent.keyDown(window, { key: "Escape" });
    await settle();
    assert.equal(dialogOpen("Details"), false);
    assert.equal(focusedId(), "open-details");
});

function SelfDisablingOpener() {
    const [open, setOpen] = useState(false);
    const [pending, setPending] = useState(false);
    // Like the campaign Start button: it disables itself (losing focus to the
    // page body) before the dialog it asked for is open.
    const start = () => {
        setPending(true);
        setTimeout(() => { setOpen(true); setPending(false); }, 5);
    };
    return (
        <>
            <button id="start" type="button" disabled={pending} onClick={start}>Start</button>
            <Modal open={open} onOpenChange={setOpen} title="Who speaks first?">
                <button id="confirm" type="button">Confirm</button>
            </Modal>
        </>
    );
}

test("focus returns to an opener that disabled itself while the dialog was opening", async () => {
    render(<SelfDisablingOpener />);
    const start = document.getElementById("start") as HTMLButtonElement;
    start.focus();
    fireEvent.click(start);
    // The browser drops focus from a control that becomes disabled.
    assert.equal(start.disabled, true);
    start.blur();
    await settle();
    await settle();
    assert.equal(dialogOpen("Who speaks first?"), true);
    assert.equal(start.disabled, false);
    fireEvent.keyDown(window, { key: "Escape" });
    await settle();
    assert.equal(dialogOpen("Who speaks first?"), false);
    assert.equal(focusedId(), "start");
});
