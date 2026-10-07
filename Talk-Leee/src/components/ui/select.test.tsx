/**
 * Shared Select — rendered, not grepped (same policy as info-tip.test.tsx).
 *
 * Covers the behaviours the select-migration work depends on:
 *  - Escape closes ONLY the panel when the Select sits inside a Modal; the
 *    modal must stay open (modal.tsx closes on a window keydown, so the Select
 *    has to stop the handled Escape from reaching it).
 *  - Optional id / aria-invalid passthrough, so <Label htmlFor> association
 *    and error styling survive migration from native <select>.
 *  - Type-ahead while the panel is open (native selects jump on typed text).
 *  - Arrow keys skip disabled options (native selects never land on them).
 *
 * Layout (clamping/flipping) is not observable in jsdom — that lives in the
 * Playwright checks, not here.
 */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React, { useState } from "react";

import { Modal } from "@/components/ui/modal";
import { Select } from "@/components/ui/select";
import { ensureDom } from "@/test-utils/dom";

ensureDom();
afterEach(() => cleanup());

// jsdom has no layout engine; the panel scrolls its active option into view.
if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = () => {};
}

function Harness({
    initial = "a",
    onValue,
    selectProps,
    options,
}: {
    initial?: string;
    onValue?: (v: string) => void;
    selectProps?: Partial<React.ComponentProps<typeof Select>>;
    options?: React.ReactNode;
}) {
    const [value, setValue] = useState(initial);
    return (
        <Select
            value={value}
            onChange={(next) => {
                setValue(next);
                onValue?.(next);
            }}
            ariaLabel="Flavour"
            {...selectProps}
        >
            {options ?? [
                <option key="a" value="a">Apple</option>,
                <option key="b" value="b">Banana</option>,
                <option key="c" value="c">Cherry</option>,
            ]}
        </Select>
    );
}

const trigger = () => screen.getByRole("combobox", { name: "Flavour" });
const panel = () => screen.queryByRole("listbox", { name: "Flavour" });

// ── Escape inside a Modal (prerequisite 0a) ─────────────────────────────────

test("Escape with the panel open closes the panel but NOT an enclosing Modal", async () => {
    const user = userEvent.setup();
    let modalOpen = true;
    render(
        <Modal open onOpenChange={(next) => { modalOpen = next; }} title="Add SIP trunk">
            <Harness />
        </Modal>,
    );

    await user.click(trigger());
    await waitFor(() => assert.ok(panel(), "panel must open"));

    await user.keyboard("{Escape}");

    await waitFor(() => assert.equal(panel(), null, "Escape must close the panel"));
    assert.equal(modalOpen, true, "the modal must survive the Escape that closed the panel");
});

test("Escape with the panel CLOSED still reaches the Modal and closes it", async () => {
    const user = userEvent.setup();
    let modalOpen = true;
    render(
        <Modal open onOpenChange={(next) => { modalOpen = next; }} title="Add SIP trunk">
            <Harness />
        </Modal>,
    );

    trigger().focus();
    await user.keyboard("{Escape}");

    await waitFor(() => assert.equal(modalOpen, false, "an unconsumed Escape must still close the modal"));
});

// ── id / aria-invalid passthrough (prerequisite 0b) ─────────────────────────

test("id lands on the trigger so <label htmlFor> keeps its association", () => {
    render(
        <div>
            <label htmlFor="flavour-select">Flavour field</label>
            <Harness selectProps={{ id: "flavour-select" }} />
        </div>,
    );

    const byLabel = screen.getByLabelText("Flavour field");
    assert.equal(byLabel.getAttribute("id"), "flavour-select");
    assert.equal(byLabel.tagName, "BUTTON");
    assert.equal(byLabel.getAttribute("role"), "combobox");
});

test("ariaInvalid reflects as aria-invalid on the trigger; absent when not set", () => {
    const { unmount } = render(<Harness selectProps={{ ariaInvalid: true }} />);
    assert.equal(trigger().getAttribute("aria-invalid"), "true");
    unmount();

    render(<Harness />);
    assert.equal(trigger().getAttribute("aria-invalid"), null, "no attribute unless asked for");
});

// ── type-ahead (migration parity for long lists) ────────────────────────────

test("typing while open jumps the active option to the first label match", async () => {
    const user = userEvent.setup();
    const picked: string[] = [];
    render(<Harness onValue={(v) => picked.push(v)} />);

    await user.click(trigger());
    await waitFor(() => assert.ok(panel()));

    await user.keyboard("ch");
    await user.keyboard("{Enter}");

    await waitFor(() => assert.deepEqual(picked, ["c"], "typing 'ch' then Enter must select Cherry"));
});

test("type-ahead ignores disabled options", async () => {
    const user = userEvent.setup();
    const picked: string[] = [];
    render(
        <Harness
            onValue={(v) => picked.push(v)}
            options={[
                <option key="a" value="a">Apple</option>,
                <option key="b" value="b" disabled>Blocked banana</option>,
                <option key="c" value="c">Banana fresh</option>,
            ]}
        />,
    );

    await user.click(trigger());
    await waitFor(() => assert.ok(panel()));

    await user.keyboard("b");
    await user.keyboard("{Enter}");

    await waitFor(() => assert.deepEqual(picked, ["c"], "the disabled 'B…' option must be skipped"));
});

// ── arrow keys skip disabled options ────────────────────────────────────────

test("ArrowDown skips a disabled option instead of landing on it", async () => {
    const user = userEvent.setup();
    const picked: string[] = [];
    render(
        <Harness
            onValue={(v) => picked.push(v)}
            options={[
                <option key="a" value="a">Apple</option>,
                <option key="b" value="b" disabled>Banana (out of season)</option>,
                <option key="c" value="c">Cherry</option>,
            ]}
        />,
    );

    await user.click(trigger());
    await waitFor(() => assert.ok(panel()));

    await user.keyboard("{ArrowDown}{Enter}"); // from Apple: must land on Cherry, not Banana

    await waitFor(() => assert.deepEqual(picked, ["c"]));
});

test("ArrowUp skips a disabled option instead of landing on it", async () => {
    const user = userEvent.setup();
    const picked: string[] = [];
    render(
        <Harness
            initial="c"
            onValue={(v) => picked.push(v)}
            options={[
                <option key="a" value="a">Apple</option>,
                <option key="b" value="b" disabled>Banana (out of season)</option>,
                <option key="c" value="c">Cherry</option>,
            ]}
        />,
    );

    await user.click(trigger());
    await waitFor(() => assert.ok(panel()));

    await user.keyboard("{ArrowUp}{Enter}"); // from Cherry: must land on Apple, not Banana

    await waitFor(() => assert.deepEqual(picked, ["a"]));
});

// ── regression guards for existing callers ──────────────────────────────────

test("plain open, click an option, value commits and panel closes", async () => {
    const user = userEvent.setup();
    const picked: string[] = [];
    render(<Harness onValue={(v) => picked.push(v)} />);

    await user.click(trigger());
    await waitFor(() => assert.ok(panel()));

    await user.click(screen.getByRole("option", { name: "Banana" }));

    await waitFor(() => assert.equal(panel(), null));
    assert.deepEqual(picked, ["b"]);
    assert.match(trigger().textContent ?? "", /Banana/);
});

test("outside pointerdown closes the panel", async () => {
    const user = userEvent.setup();
    render(
        <div>
            <Harness />
            <button type="button">Elsewhere</button>
        </div>,
    );

    await user.click(trigger());
    await waitFor(() => assert.ok(panel()));

    await user.pointer({ keys: "[MouseLeft>]", target: screen.getByRole("button", { name: "Elsewhere" }) });

    await waitFor(() => assert.equal(panel(), null));
});
