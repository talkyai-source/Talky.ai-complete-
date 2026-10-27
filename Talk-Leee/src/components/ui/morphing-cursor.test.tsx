import assert from "node:assert/strict";
import test, { afterEach } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, render } from "@testing-library/react";

import { MagneticText } from "./morphing-cursor";

afterEach(() => {
    cleanup();
    delete (window as unknown as { matchMedia?: unknown }).matchMedia;
});

function stubMatchMedia(matches: boolean) {
    window.matchMedia = ((query: string) => ({
        matches,
        media: query,
        onchange: null,
        addEventListener() {},
        removeEventListener() {},
        addListener() {},
        removeListener() {},
        dispatchEvent() { return false; },
    })) as unknown as typeof window.matchMedia;
}

test("the hover copy is decorative: assistive technology sees the text once", () => {
    stubMatchMedia(false);
    const { container } = render(<MagneticText text="AI VOICE" hoverText="AI VOICE" />);
    const spans = [...container.querySelectorAll("span")].filter((s) => s.textContent === "AI VOICE");
    assert.equal(spans.length, 2, "visible copy plus hover copy are rendered");
    const exposed = spans.filter((s) => !s.closest("[aria-hidden='true']"));
    assert.equal(exposed.length, 1);
});

test("with motion allowed, entering the heading starts the magnetic hover effect", () => {
    stubMatchMedia(false);
    const { container } = render(<MagneticText text="AI VOICE" hoverText="AI VOICE" />);
    const circle = container.querySelector("[aria-hidden='true']") as HTMLElement;
    act(() => { fireEvent.mouseEnter(container.firstElementChild!, { clientX: 10, clientY: 10 }); });
    assert.match(circle.style.transform, /scale\(1\)$/);
    assert.match(circle.style.transition, /transform 0\.5s/);
});

test("with prefers-reduced-motion: reduce, the heading stays static", () => {
    stubMatchMedia(true);
    const { container } = render(<MagneticText text="AI VOICE" hoverText="AI VOICE" />);
    const circle = container.querySelector("[aria-hidden='true']") as HTMLElement;
    act(() => { fireEvent.mouseEnter(container.firstElementChild!, { clientX: 10, clientY: 10 }); });
    assert.match(circle.style.transform, /scale\(0\)$/);
    assert.equal(circle.style.transition, "none");
});

// The wrapper used to carry Tailwind's `cursor-none`, which hid the system
// pointer over the heading: with motion allowed only the lagging disc was
// visible, and under reduced motion nothing was. The pointer is drawn by the
// OS, so no z-index can bring it back; the only rule that can hide it is a
// `cursor: none` on an element under it, and there must be none.
for (const [label, reduce] of [["motion allowed", false], ["prefers-reduced-motion: reduce", true]] as const) {
    test(`${label}: the system pointer is never hidden over the heading`, () => {
        stubMatchMedia(reduce);
        const { container } = render(<MagneticText text="AI VOICE" hoverText="AI VOICE" />);
        const wrapper = container.firstElementChild as HTMLElement;
        act(() => { fireEvent.mouseEnter(wrapper, { clientX: 10, clientY: 10 }); });
        for (const el of [wrapper, ...wrapper.querySelectorAll<HTMLElement>("*")]) {
            assert.ok(!el.classList.contains("cursor-none"), `${el.tagName} hides the pointer with cursor-none`);
            assert.equal(el.style.cursor, "", `${el.tagName} hides the pointer with an inline cursor rule`);
        }
    });
}
