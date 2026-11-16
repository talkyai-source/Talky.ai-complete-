/**
 * A Select must not schedule work of its own just because its parent redrew.
 *
 * Its options are read from `children`, which is a new array on every render
 * of the parent. Two effects used that list as a dependency, so each of them
 * ran again after every parent render and set state (the panel position, the
 * fitted width). On a page with a Select, every key typed into any other field
 * therefore cost a second render, and when input arrived faster than React
 * could finish that second render, React's loop guard counted fifty of them
 * in a row and threw "Maximum update depth exceeded" from the field's own
 * onChange: the key was lost. Seen on the live create-campaign page on
 * 14 Nov 2026 as one character lost in about fifty.
 */
import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import React, { Profiler, useState } from "react";

import { Select } from "@/components/ui/select";
import { ensureDom } from "@/test-utils/dom";

ensureDom();
afterEach(() => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    cleanup();
    mock.restoreAll();
});

/** A text measurer, so the fitted-width effect has something to measure with. */
function withCanvas() {
    mock.method(window.HTMLCanvasElement.prototype, "getContext", () => ({
        font: "",
        measureText: (text: string) => ({ width: text.length * 7 }),
    }));
}

function Form({ fit = false, labels = ["1 attempt", "2 attempts", "3 attempts"] }: { fit?: boolean; labels?: string[] }) {
    const [text, setText] = useState("");
    return (
        <>
            <input aria-label="Name" value={text} onChange={(event) => setText(event.target.value)} />
            <Select ariaLabel="Attempts" value="2" onChange={() => undefined} fitLongestOption={fit}>
                {labels.map((label, index) => <option key={label} value={String(index + 1)}>{label}</option>)}
            </Select>
        </>
    );
}

for (const fit of [false, true]) {
    test(`typing in another field costs one render, not two (fitLongestOption=${fit})`, async () => {
        withCanvas();
        const commits: string[] = [];
        render(<Profiler id="form" onRender={(_id, phase) => { commits.push(phase); }}><Form fit={fit} /></Profiler>);
        const name = screen.getByLabelText("Name");
        for (const value of ["a", "ab", "abc"]) {
            commits.length = 0;
            await act(async () => { fireEvent.change(name, { target: { value } }); });
            assert.deepEqual(commits, ["update"], `typing "${value}" must not make the Select render again by itself`);
        }
    });

    test(`a fast run of 120 key presses loses no character (fitLongestOption=${fit})`, () => {
        withCanvas();
        render(<Form fit={fit} />);
        const name = screen.getByLabelText("Name") as HTMLInputElement;
        const errors: string[] = [];
        const onError = (event: ErrorEvent) => { errors.push(String(event.error ?? event.message)); event.preventDefault(); };
        window.addEventListener("error", onError);
        // Real browser input is not wrapped in act(), and these events arrive
        // back to back: React gets no pause in which to finish follow-up work.
        (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = false;
        const full = Array.from({ length: 120 }, (_, index) => "abcdefghij"[index % 10]).join("");
        const setValue = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")!.set!;
        try {
            for (let length = 1; length <= full.length; length += 1) {
                setValue.call(name, full.slice(0, length));
                name.dispatchEvent(new window.Event("input", { bubbles: true }));
            }
        } finally {
            window.removeEventListener("error", onError);
        }
        assert.deepEqual(errors, [], "no error while typing");
        assert.equal(name.value, full, "every character is in the field");
    });
}

test("options that really change are still picked up", async () => {
    withCanvas();
    const view = render(<Form fit labels={["One", "Two"]} />);
    assert.match(screen.getByRole("combobox", { name: "Attempts" }).textContent ?? "", /Two/);
    const before = screen.getByRole("combobox", { name: "Attempts" }).style.minWidth;
    await act(async () => { view.rerender(<Form fit labels={["One", "A much longer second option"]} />); });
    const trigger = screen.getByRole("combobox", { name: "Attempts" });
    assert.match(trigger.textContent ?? "", /A much longer second option/);
    assert.notEqual(trigger.style.minWidth, before, "the fitted width follows the new longest option");
});
