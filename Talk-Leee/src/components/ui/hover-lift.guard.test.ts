/**
 * Guard for every box that lifts 2px on hover (the class hover:-translate-y-0.5).
 *
 * The lift takes the box's bottom 2px out from under a pointer resting there, so the box drops, is
 * hovered again and lifts again on every frame. Each lifting box therefore carries, while hovered, an
 * invisible strip over the space it left (explained in card.tsx). This guard reads the source and fails
 * when a lifting box is added, or edited, without that strip. It is a guard on the class lists; that the
 * boxes really stay still is measured in the browser by tests/hover-lift.spec.ts.
 *
 * A box that clips its own content (overflow: hidden) would clip the strip with the rest. So no lifting
 * box may clip itself: where content must be clipped, a wrapper inside the box does it (the two image
 * frames, the home services card and the two Billing overview cards are built that way).
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";

const SRC = path.join(process.cwd(), "src");
const LIFT = "hover:-translate-y-0.5";
const CARD = "src/components/ui/card.tsx";
const STRIP_PARTS = ["hover:after:absolute", "hover:after:inset-x-0", "hover:after:top-full", "hover:after:box-content", "hover:after:border-transparent", "hover:after:[border-bottom-width:inherit]", "hover:after:content-['']"];
/** The strip is 2px high, the height of the lift. */
const HEIGHT_PLAIN = "hover:after:h-0.5";
/** A box that also grows by 1% on hover moves its bottom edge down by 0.5% of its height, so its strip is that much shorter. */
const HEIGHT_GROWS = "hover:after:h-[calc((2px-0.5%)/1.01)]";
/**
 * The analytics figure lifts 2px and grows by 2% in one animation (whileHover on the same element), not by classes, so
 * the two are undone together. Its strip is (2px - 1% of its height) / 1.02 when fully hovered, times the animation's
 * progress ("--analytics-lift", 0 to 1), so the strip ends at the figure's at-rest edge all the way through.
 */
const HEIGHT_GROWS_2 = "hover:after:h-[calc(var(--analytics-lift)*(2px-1%)/1.02)]";
const GROWS_2_ANIMATION = 'whileHover={{ scale: 1.02, y: -2, "--analytics-lift": 1 }}';
const GROWS_2_FILE = "src/app/analytics/page.tsx";
/** Classes that clip a box's content: on the box itself they would clip its strip. "home-services-card" clips through globals.css. */
const CLIPS = /(^|[\s"'`])(overflow-hidden|overflow-clip|overflow-auto|overflow-scroll|overflow-[xy]-(hidden|clip|auto|scroll)|home-services-card|home-packages-card)([\s"'`!]|$)/;

function sourceFiles(dir: string, out: string[] = []): string[] {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) sourceFiles(full, out);
        else if (/\.tsx?$/.test(entry.name) && !/\.(test|stories)\./.test(entry.name)) out.push(full);
    }
    return out;
}

type Lift = { where: string; file: string; line: string; classes: string };
const lifts: Lift[] = [];
/** Every use of the shared Card whose own class list clips (the Card lifts, so it must not). */
const clippingCards: string[] = [];
for (const file of sourceFiles(SRC)) {
    const text = fs.readFileSync(file, "utf8");
    const lines = text.split(/\r?\n/);
    const rel = path.relative(process.cwd(), file).replace(/\\/g, "/");
    lines.forEach((line, i) => {
        // card.tsx keeps the strip in its own string a few lines under the lift, below a comment.
        // The analytics figure has no lift class (its lift is in its animation): it is found by its strip.
        if (line.includes(LIFT) || (rel === GROWS_2_FILE && line.includes("hover:after:top-full"))) lifts.push({ where: `${rel}:${i + 1}`, file: rel, line, classes: rel === CARD ? lines.slice(i, i + 10).join("\n") : line });
        const card = /<Card\b[^>]*className="([^"]*)"/.exec(line);
        if (card && CLIPS.test(card[1])) clippingCards.push(`${rel}:${i + 1}`);
    });
}

test("the app's lifting boxes are found", () => {
    assert.ok(lifts.length >= 49, `expected at least 49 lifting boxes, found ${lifts.length}`);
    assert.ok(lifts.some((lift) => lift.file === CARD));
});

test("every lifting box has the strip under it while hovered", () => {
    const missing: string[] = [];
    for (const lift of lifts) for (const part of STRIP_PARTS) if (!lift.classes.includes(part)) missing.push(`${lift.where}: ${part}`);
    assert.deepEqual(missing, []);
});

test("the strip is 2px high, and shorter by the growth on a box that also grows on hover", () => {
    const wrong: string[] = [];
    for (const lift of lifts) {
        const grows = /hover:scale-\[/.test(lift.line);
        if (grows && !lift.line.includes("hover:scale-[1.01]")) wrong.push(`${lift.where}: grows by something other than 1%; its strip height must be worked out again`);
        const grows2 = lift.file === GROWS_2_FILE;
        if (grows2) {
            const source = fs.readFileSync(path.join(process.cwd(), lift.file), "utf8");
            if (!source.includes(GROWS_2_ANIMATION)) wrong.push(`${lift.where}: no longer lifts 2px and grows by 2% in one animation; its strip height must be worked out again`);
            // A lift class as well would lift it twice, and be undone at once while the growth shrinks back.
            if (source.includes(LIFT)) wrong.push(`${lift.where}: lifts by a class as well as by its animation`);
            for (const rest of ['initial={{ opacity: 0, y: 20, "--analytics-lift": 0 }}', 'animate={{ opacity: 1, y: 0, "--analytics-lift": 0 }}']) if (!source.includes(rest)) wrong.push(`${lift.where}: the strip's share is not 0 at rest (${rest})`);
        }
        const want = grows2 ? HEIGHT_GROWS_2 : grows ? HEIGHT_GROWS : HEIGHT_PLAIN;
        const heights = [HEIGHT_PLAIN, HEIGHT_GROWS, HEIGHT_GROWS_2].filter((height) => lift.classes.includes(height));
        if (heights.length !== 1 || heights[0] !== want) wrong.push(`${lift.where}: expected ${want}`);
    }
    assert.deepEqual(wrong, []);
});

test("the strip exists only while the box is hovered", () => {
    const wrong: string[] = [];
    // An "after:" class without "hover:" in front would give the box a strip at rest too.
    for (const lift of lifts) for (const token of lift.classes.split(/[\s"'`]+/)) if (/(^|:)after:/.test(token) && !token.startsWith("hover:after:")) wrong.push(`${lift.where}: ${token}`);
    assert.deepEqual(wrong, []);
});

test("no lifting box clips its own content: a wrapper inside it does", () => {
    const clipping = lifts.filter((lift) => CLIPS.test(lift.line)).map((lift) => lift.where);
    assert.deepEqual([...clipping, ...clippingCards], []);
});

test("the five boxes whose content is clipped keep that clipping on a wrapper at their inner edge", () => {
    const read = (file: string) => fs.readFileSync(path.join(process.cwd(), file), "utf8");
    // Inner corner = the box's corner less its 1px border (the shared Card's corner is var(--radius) + 4px).
    const frame = read("src/app/ai-voice-agent/page.tsx");
    assert.ok(frame.includes('const imageClipClassName = "overflow-hidden rounded-[calc(1.5rem-1px)]";') && frame.includes("<div className={imageClipClassName}>"));
    assert.ok(read("src/app/use-cases/customer-services-support/page.tsx").includes('<div className="overflow-hidden rounded-[calc(1.5rem-1px)]">'));
    const billing = read("src/components/billing/billing-overview.tsx");
    assert.equal(billing.split('<div className="relative overflow-hidden rounded-[calc(var(--radius)+3px)]">').length - 1, 2);
    const services = read("src/components/home/features-section.tsx");
    // The wrapper clips through "home-services-card"; the card paints the background; the sweep keeps the card's own corner and follows the card's hover.
    assert.ok(services.includes('<div className="home-services-card bg-none! h-full rounded-[calc(1rem-1px)] p-8 before:rounded-2xl! group-hover:before:opacity-100! group-hover:before:[transform:translate3d(0,0,0)]!">'));
    assert.ok(/className="group h-full rounded-2xl border border-gray-200 bg-transparent bg-\(image:--home-card-gradient\) bg-cover bg-no-repeat /.test(services));
});
