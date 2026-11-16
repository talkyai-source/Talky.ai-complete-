/**
 * The last action of a dashboard page must not sit under the floating Help and
 * Assistant buttons. Live test, 14 Nov 2026: the wizard's bottom-right button
 * was covered by Help, because the page left room for the Assistant button
 * only (the Help button was added above it later).
 *
 * jsdom has no layout engine, so this reads the classes that set the sizes
 * and checks the arithmetic at each screen width. It is a guard on those
 * numbers; the live page was checked by eye as well.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
const layout = read("./dashboard-layout.tsx");
const help = read("../support/help-button.tsx");

const REM = 4; // one Tailwind spacing unit, in px

/** Value of a spacing class such as `pb-32` or `md:p-8`, in px; undefined when the class is absent. */
function spacing(classes: string, name: string): number | undefined {
    const match = classes.split(/\s+/).map((item) => /^(.+)-(\d+)$/.exec(item)).find((parts) => parts?.[1] === name);
    return match ? Number(match[2]) * REM : undefined;
}

function classOf(source: string, marker: RegExp): string {
    const match = marker.exec(source);
    assert.ok(match, `could not find ${marker}`);
    return match[1];
}

test("the page leaves room for both floating buttons at every screen width", () => {
    const main = classOf(layout, /<main id="main-content" className="([^"]+)"/);
    const inner = classOf(layout, /<div className="(mx-auto flex w-full max-w-7xl[^"]+)"/);
    const button = classOf(help, /"(fixed bottom-\d+[^"]+)"/);

    const height = spacing(button, "h");
    const innerPad = spacing(inner, "pb");
    assert.ok(height && innerPad, "the Help button height and the page's bottom padding are set");

    // Top edge of the Help button above the bottom of the screen.
    const helpTop = { small: spacing(button, "bottom")! + height, sm: (spacing(button, "sm:bottom") ?? spacing(button, "bottom")!) + height };
    // Room below the last content: main's own padding plus the inner padding.
    const room = { small: spacing(main, "p")! + innerPad, md: (spacing(main, "md:p") ?? spacing(main, "p")!) + innerPad };

    assert.ok(room.small >= helpTop.small, `phone: ${room.small}px of room for a button that reaches ${helpTop.small}px`);
    // Between the sm and md widths the button is already at its higher place
    // while main still has its small padding: the tightest case.
    assert.ok(room.small >= helpTop.sm, `tablet: ${room.small}px of room for a button that reaches ${helpTop.sm}px`);
    assert.ok(room.md >= helpTop.sm, `desktop: ${room.md}px of room for a button that reaches ${helpTop.sm}px`);
});
