/**
 * The shared Card: its hover lift and shadow are unchanged, and the strip that
 * keeps a resting pointer on a lifted card is in place. jsdom has no pointer
 * or layout, so this guards the classes; that a card really stays still with
 * the pointer about 3px above its bottom edge is measured in
 * tests/telephony-buy-now.spec.ts.
 */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, render } from "@testing-library/react";
import { Card } from "./card";

afterEach(cleanup);

const classesOf = (el: Element) => Array.from(el.classList);

test("the card's look, its 2px hover lift and its hover shadow are unchanged", () => {
    const { container } = render(<Card>content</Card>);
    const classes = classesOf(container.firstElementChild as Element);
    for (const kept of [
        "rounded-xl", "border", "border-gray-200", "dark:border-slate-700", "bg-white", "dark:bg-slate-900", "text-gray-900", "dark:text-white", "shadow-sm",
        "transition-[transform,background-color,box-shadow,border-color]", "duration-150", "ease-out",
        "hover:-translate-y-0.5", "hover:bg-gray-50", "dark:hover:bg-slate-800", "hover:shadow-md", "dark:hover:shadow-lg",
    ]) {
        assert.equal(classes.includes(kept), true, kept);
    }
});

test("while hovered, a 2px strip below the lifted card keeps a resting pointer on it", () => {
    const { container } = render(<Card>content</Card>);
    const classes = classesOf(container.firstElementChild as Element);
    // 2px for the lift, plus the card's own bottom border width (inherited), measured from inside that border.
    for (const strip of ["hover:after:absolute", "hover:after:inset-x-0", "hover:after:top-full", "hover:after:box-content", "hover:after:h-0.5", "hover:after:border-transparent", "hover:after:[border-bottom-width:inherit]", "hover:after:content-['']"]) {
        assert.equal(classes.includes(strip), true, strip);
    }
    // Only on hover: nothing is added to a card at rest, and its positioning is not changed.
    assert.deepEqual(classes.filter((name) => name.includes("after:") && !name.startsWith("hover:after:")), []);
    for (const position of ["relative", "absolute", "fixed", "sticky", "hover:relative"]) assert.equal(classes.includes(position), false, position);
});

test("a card keeps the classes it is given", () => {
    const { container } = render(<Card className="flex flex-col p-2">content</Card>);
    const classes = classesOf(container.firstElementChild as Element);
    for (const given of ["flex", "flex-col", "p-2", "hover:-translate-y-0.5", "hover:after:top-full"]) assert.equal(classes.includes(given), true, given);
});
