/**
 * FX7c Part 0 — the live chart, three follow-ups to FX7b.
 * 0.1: the wheel zooms around the point under the mouse and a note is added to
 *      the point that was clicked, at every drawn width.
 * 0.2: the keyboard's hover box is placed at its point's real position on screen.
 * 0.3: on day buckets the bottom labels print the date in the zone the read
 *      used; hour buckets still print times.
 */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { LiveCallsTimeSeriesChart, type LiveTimeBucket } from "./dashboard-charts";
import { formatZoneDateLabel } from "@/lib/utc-date-label";

// The hover box follows its position on animation frames; the test window has none.
const w = window as unknown as { requestAnimationFrame?: (cb: FrameRequestCallback) => number; cancelAnimationFrame?: (id: number) => void };
if (typeof w.requestAnimationFrame !== "function") {
    w.requestAnimationFrame = (cb) => setTimeout(() => cb(performance.now()), 0) as unknown as number;
    w.cancelAnimationFrame = (id) => clearTimeout(id);
}

// The hover box is kept inside the window; the widest chart here ends at x = 1524.
Object.defineProperty(window, "innerWidth", { value: 1920, configurable: true });
Object.defineProperty(window, "innerHeight", { value: 1080, configurable: true });

afterEach(() => { cleanup(); });

const HOUR = 60 * 60 * 1000;
const ZONE = "Asia/Karachi";
const firstHour = Date.parse("2026-11-16T10:00:00Z");
function hours(totals: number[]): LiveTimeBucket[] {
    return totals.map((total, i) => ({
        startMs: firstHour + i * HOUR, endMs: firstHour + (i + 1) * HOUR, total, answered: 0, failed: 0, avgDurationSec: null,
    }));
}
// Five day buckets cut at Asia/Karachi midnight (19:00Z the day before).
const firstDay = Date.parse("2026-10-31T19:00:00Z");
const dayBuckets: LiveTimeBucket[] = Array.from({ length: 5 }, (_, i) => ({
    startMs: firstDay + i * 24 * HOUR, endMs: firstDay + (i + 1) * 24 * HOUR, total: i + 1, answered: i, failed: 0, avgDurationSec: null,
}));

// The chart's drawing: 980 by 320 units, the plot from x=70 to x=956 and y=26 to y=264.
const DRAW_W = 980;
const DRAW_H = 320;
const PLOT_LEFT = 70;
const PLOT_W = 980 - 70 - 24;
const PLOT_TOP = 26;
const PLOT_BOTTOM = 264;
const RECT_LEFT = 24;
const RECT_TOP = 100;

function drawnAt(svg: Element, width: number, height = DRAW_H) {
    (svg as unknown as { getBoundingClientRect: () => DOMRect }).getBoundingClientRect = () =>
        ({ left: RECT_LEFT, top: RECT_TOP, width, height, right: RECT_LEFT + width, bottom: RECT_TOP + height, x: RECT_LEFT, y: RECT_TOP, toJSON() {} }) as DOMRect;
    const scale = Math.min(width / DRAW_W, height / DRAW_H);
    return {
        x: (drawingX: number) => RECT_LEFT + (width - DRAW_W * scale) / 2 + drawingX * scale,
        y: (drawingY: number) => RECT_TOP + (height - DRAW_H * scale) / 2 + drawingY * scale,
    };
}
const pointX = (i: number, n: number) => PLOT_LEFT + (i * PLOT_W) / (n - 1);
const chartSvg = () => screen.getByRole("img", { name: "Live calls time-series chart" });
const hhmm = (ms: number) => new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", timeZone: ZONE });

// Element widths the chart is drawn at from a 360 px phone to a 1920 px screen.
const WIDTHS = [280, 310, 640, 720, 980, 1100, 1500];

for (const width of WIDTHS) {
    test(`0.1: drawn ${width} px wide, a click in note mode adds the note to the point that was clicked`, () => {
        const buckets = hours([1, 2, 3, 4, 5]);
        const added: number[] = [];
        render(<LiveCallsTimeSeriesChart buckets={buckets} timeZone={ZONE} noteMode onAddNoteAtMs={(ms) => added.push(ms)} />);
        const svg = chartSvg();
        const on = drawnAt(svg, width);
        buckets.forEach((bucket, i) => {
            fireEvent(svg, new MouseEvent("pointerdown", { bubbles: true, clientX: on.x(pointX(i, buckets.length)), clientY: 200 }));
            assert.equal(added.length, i + 1, `one note per click (point ${i})`);
            assert.equal(added.at(-1), bucket.startMs, `point ${i}`);
        });
    });

    test(`0.1: drawn ${width} px wide, the wheel zooms around the point under the mouse`, () => {
        const buckets = hours([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]);
        // Newest point, then a point in the middle: each stays under the mouse after one zoom step.
        for (const at of [11, 6]) {
            const active: Array<number | null> = [];
            render(<LiveCallsTimeSeriesChart buckets={buckets} timeZone={ZONE} onActiveBucketChange={(b) => active.push(b?.startMs ?? null)} />);
            const svg = chartSvg();
            const on = drawnAt(svg, width);
            const clientX = on.x(pointX(at, buckets.length));
            fireEvent.mouseMove(svg, { clientX, clientY: 200 });
            assert.equal(active.at(-1), buckets[at]!.startMs, `before the zoom, point ${at}`);
            fireEvent.wheel(svg, { clientX, clientY: 200, deltaY: -100 });
            fireEvent.mouseMove(svg, { clientX, clientY: 200 });
            assert.equal(active.at(-1), buckets[at]!.startMs, `after the zoom, point ${at}`);
            // The zoom really happened: the left edge no longer opens the first bucket.
            fireEvent.mouseMove(svg, { clientX: on.x(PLOT_LEFT), clientY: 200 });
            assert.equal(active.at(-1) === buckets[0]!.startMs, false, "zoomed in");
            cleanup();
        }
    });

    test(`0.2: drawn ${width} px wide, the keyboard's hover box is placed at its own point on screen`, () => {
        // Totals 0 and 5 sit on the plot's bottom and top lines (the axis runs 0 to 5).
        const buckets = hours([0, 1, 2, 3, 5]);
        render(<LiveCallsTimeSeriesChart buckets={buckets} timeZone={ZONE} />);
        const svg = chartSvg();
        const on = drawnAt(svg, width);
        // The hover box is drawn above its anchor: translate3d(anchorX - width / 2, anchorY - 12 - height),
        // and the test window measures it 0 by 0 (hover-tooltip.tsx applyPosition).
        const anchor = () => {
            const m = /translate3d\(([-\d.]+)px, ([-\d.]+)px/.exec(screen.getByRole("tooltip").style.transform);
            assert.equal(m !== null, true, "hover box positioned");
            return { x: Number(m![1]), y: Number(m![2]) + 12 };
        };
        const near = (a: number, b: number) => Math.abs(a - b) < 0.01;
        const expectedY = [PLOT_BOTTOM, null, null, null, PLOT_TOP];
        fireEvent.keyDown(svg, { key: "Home" });
        for (let i = 0; i < buckets.length; i++) {
            if (i > 0) fireEvent.keyDown(svg, { key: "ArrowRight" });
            assert.equal(screen.getByRole("tooltip").textContent?.startsWith(`${hhmm(buckets[i]!.startMs)}–`), true, `box of point ${i}`);
            const at = anchor();
            assert.equal(near(at.x, on.x(pointX(i, buckets.length))), true, `x of point ${i}: ${at.x} vs ${on.x(pointX(i, buckets.length))}`);
            const y = expectedY[i];
            if (typeof y === "number") assert.equal(near(at.y, on.y(y)), true, `y of point ${i}: ${at.y} vs ${on.y(y)}`);
        }
    });
}

function bottomLabels(svg: Element) {
    return Array.from(svg.querySelectorAll("text")).map((t) => t.textContent ?? "");
}

test("0.3: day buckets print their date on the bottom labels, in the zone the read used", () => {
    render(<LiveCallsTimeSeriesChart buckets={dayBuckets} timeZone={ZONE} />);
    const labels = bottomLabels(chartSvg());
    // The four labelled buckets of five: first, second, third and newest.
    const expected = [0, 1, 2, 4].map((i) => formatZoneDateLabel(dayBuckets[i]!.startMs, ZONE));
    assert.equal(new Set(expected).size, 4, "four distinct dates");
    for (const date of expected) {
        assert.equal(date.length > 0, true);
        assert.equal(labels.filter((l) => l === date).length, 1, `${date} in ${JSON.stringify(labels)}`);
    }
    // No label is the midnight time every day bucket starts at.
    assert.equal(labels.includes(hhmm(dayBuckets[0]!.startMs)), false, JSON.stringify(labels));
    // The bucket that starts 2026-10-31T19:00Z is 1 November in Asia/Karachi, not 31 October.
    assert.equal(expected[0], new Date(Date.parse("2026-11-01T12:00:00Z")).toLocaleDateString(undefined, { timeZone: "UTC" }));
});

test("0.3: hour buckets still print times on the bottom labels", () => {
    const buckets = hours([1, 2, 3, 4, 5]);
    render(<LiveCallsTimeSeriesChart buckets={buckets} timeZone={ZONE} />);
    const labels = bottomLabels(chartSvg());
    for (const i of [0, 1, 2, 4]) {
        assert.equal(labels.filter((l) => l === hhmm(buckets[i]!.startMs)).length, 1, `${hhmm(buckets[i]!.startMs)} in ${JSON.stringify(labels)}`);
    }
    assert.equal(labels.includes(formatZoneDateLabel(buckets[0]!.startMs, ZONE)), false, "no date label");
});
