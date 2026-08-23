/**
 * InfoTip's contract, pinned without a DOM.
 *
 * The component itself needs a browser to test properly (Radix portals, pointer
 * events, collision math), and this repo has no jsdom harness. What CAN be
 * checked here is the thing most likely to rot silently: the source no longer
 * satisfying the requirements goals.md §8 spells out.
 *
 * That sounds like a weak test, and for most components it would be. It is
 * worth having here because §8's requirements are easy to delete by accident —
 * someone "simplifying" the click handler removes mobile support entirely, and
 * nothing else in the codebase would notice.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const SOURCE = readFileSync(
    path.join(process.cwd(), "src/components/ui/info-tip.tsx"),
    "utf8",
);

test("mobile tap is supported — a click pins the tip open", () => {
    // Without this, the component is hover-only and unusable on a phone, which
    // is the specific failure §8 lists first.
    assert.match(SOURCE, /onClick=/, "the trigger must handle click/tap");
    assert.match(SOURCE, /setPinned/, "a tap must pin the tip open");
});

test("keyboard focus works because the trigger is a real button", () => {
    // A <span> with a hover handler is invisible to the keyboard. A real
    // <button> gets focus, Enter and Space for free.
    assert.match(SOURCE, /<button/, "the trigger must be a button element");
    assert.match(SOURCE, /type="button"/, "and must not submit a surrounding form");
});

test("a pinned tip can be dismissed by Escape and by an outside tap", () => {
    assert.match(SOURCE, /Escape/, "Escape must close a pinned tip");
    assert.match(SOURCE, /pointerdown/, "an outside tap must close a pinned tip");
});

test("the tip stays inside the viewport", () => {
    // The reason this is one shared component rather than nine hand-placed
    // divs: on a narrow screen an unclamped popup renders off-screen, and the
    // tap path is the only path there.
    assert.match(SOURCE, /collisionPadding/, "must pad against viewport edges");
    assert.match(SOURCE, /avoidCollisions/, "must flip/shift rather than overflow");
    assert.match(SOURCE, /max-w-\[min\(/, "width must be capped to the viewport");
});

test("every trigger has a distinct accessible name", () => {
    // `label` is required, not optional: nine buttons all announced as
    // "more info" is worse than no tooltips at all for a screen-reader user.
    assert.match(SOURCE, /label: string/, "label must be a required prop");
    assert.match(SOURCE, /aria-label=\{label\}/, "and must reach the button");
});

test("optional Learn more content is supported", () => {
    assert.match(SOURCE, /learnMoreHref/, "§8 asks for optional deeper reading");
});

test("the file records that essential warnings must not live only in a tip", () => {
    // §8: "Avoid hiding essential warnings only inside tooltips." That is a
    // usage rule, unenforceable by types — so it is written where the next
    // person to add a tip will read it.
    assert.match(
        SOURCE,
        /essential warnings|MUST NOT GO IN HERE/i,
        "the usage rule must be documented in the component",
    );
});
