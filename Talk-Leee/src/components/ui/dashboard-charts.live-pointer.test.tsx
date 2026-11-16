/**
 * FX7b Part 0 — the live chart.
 * 0.1: the mouse opens the point under it at every drawn width (the drawing is
 *      980 units wide, fitted and centred inside the element), from the first
 *      point to the newest.
 * 0.2: a day bucket's hover box prints its date in the zone its read used; an
 *      hour bucket still prints its two times.
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

afterEach(() => { cleanup(); });

const HOUR = 60 * 60 * 1000;
const ZONE = "Asia/Karachi";
// Five hour buckets, 15:00 to 19:00 in Asia/Karachi (10:00Z to 14:00Z).
const firstHour = Date.parse("2026-11-16T10:00:00Z");
const hourBuckets: LiveTimeBucket[] = Array.from({ length: 5 }, (_, i) => ({
    startMs: firstHour + i * HOUR, endMs: firstHour + (i + 1) * HOUR, total: i + 1, answered: i, failed: 0, avgDurationSec: null,
}));
// Three day buckets cut at Asia/Karachi midnight (19:00Z the day before).
const firstDay = Date.parse("2026-10-31T19:00:00Z");
const dayBuckets: LiveTimeBucket[] = Array.from({ length: 3 }, (_, i) => ({
    startMs: firstDay + i * 24 * HOUR, endMs: firstDay + (i + 1) * 24 * HOUR, total: i + 1, answered: i, failed: 0, avgDurationSec: null,
}));

// The chart's drawing: 980 by 320 units, the plot from x=70 to x=956.
const DRAW_W = 980;
const DRAW_H = 320;
const PLOT_LEFT = 70;
const PLOT_W = 980 - 70 - 24;

function drawnAt(svg: Element, left: number, width: number, height = DRAW_H) {
    (svg as unknown as { getBoundingClientRect: () => DOMRect }).getBoundingClientRect = () =>
        ({ left, top: 100, width, height, right: left + width, bottom: 100 + height, x: left, y: 100, toJSON() {} }) as DOMRect;
    const scale = Math.min(width / DRAW_W, height / DRAW_H);
    const offset = (width - DRAW_W * scale) / 2;
    /** The screen x of a drawing x. */
    return (drawingX: number) => left + offset + drawingX * scale;
}

const tooltipText = () => screen.queryByRole("tooltip")?.textContent ?? "";
const hhmm = (ms: number) => new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", timeZone: ZONE });

// Element widths the chart is drawn at from a 360 px phone to a 1920 px screen.
for (const width of [280, 310, 640, 720, 980, 1100, 1500]) {
    test(`0.1: drawn ${width} px wide, the mouse opens the point under it, first to newest`, () => {
        const active: Array<number | null> = [];
        render(<LiveCallsTimeSeriesChart buckets={hourBuckets} timeZone={ZONE} onActiveBucketChange={(b) => active.push(b?.startMs ?? null)} />);
        const svg = screen.getByRole("img", { name: "Live calls time-series chart" });
        const screenX = drawnAt(svg, 24, width);
        hourBuckets.forEach((bucket, i) => {
            const pointX = PLOT_LEFT + (i * PLOT_W) / (hourBuckets.length - 1);
            fireEvent.mouseMove(svg, { clientX: screenX(pointX), clientY: 200 });
            assert.equal(active.at(-1), bucket.startMs, `point ${i}`);
            assert.equal(tooltipText().startsWith(`${hhmm(bucket.startMs)}–${hhmm(bucket.endMs)}`), true, `hover box of point ${i}: ${tooltipText()}`);
        });
        // The right edge of the element opens the newest point, the left edge the first.
        fireEvent.mouseMove(svg, { clientX: 24 + width - 1, clientY: 200 });
        assert.equal(active.at(-1), hourBuckets[4]!.startMs, "right edge");
        fireEvent.mouseMove(svg, { clientX: 24, clientY: 200 });
        assert.equal(active.at(-1), hourBuckets[0]!.startMs, "left edge");
    });
}

test("0.1: the keyboard still reaches every point (Home, End, arrows)", () => {
    const active: Array<number | null> = [];
    render(<LiveCallsTimeSeriesChart buckets={hourBuckets} timeZone={ZONE} onActiveBucketChange={(b) => active.push(b?.startMs ?? null)} />);
    const svg = screen.getByRole("img", { name: "Live calls time-series chart" });
    drawnAt(svg, 24, 310);
    fireEvent.keyDown(svg, { key: "Home" });
    assert.equal(active.at(-1), hourBuckets[0]!.startMs);
    for (let i = 1; i < hourBuckets.length; i++) {
        fireEvent.keyDown(svg, { key: "ArrowRight" });
        assert.equal(active.at(-1), hourBuckets[i]!.startMs);
    }
    fireEvent.keyDown(svg, { key: "ArrowLeft" });
    assert.equal(active.at(-1), hourBuckets[3]!.startMs);
    fireEvent.keyDown(svg, { key: "End" });
    assert.equal(active.at(-1), hourBuckets[4]!.startMs);
});

test("0.2: a day bucket's hover box prints its date in the zone its read used", () => {
    render(<LiveCallsTimeSeriesChart buckets={dayBuckets} timeZone={ZONE} />);
    const svg = screen.getByRole("img", { name: "Live calls time-series chart" });
    const screenX = drawnAt(svg, 0, DRAW_W);
    dayBuckets.forEach((bucket, i) => {
        fireEvent.mouseMove(svg, { clientX: screenX(PLOT_LEFT + (i * PLOT_W) / (dayBuckets.length - 1)), clientY: 200 });
        const expected = formatZoneDateLabel(bucket.startMs, ZONE);
        assert.equal(expected.length > 0, true);
        assert.equal(tooltipText().startsWith(`${expected}Total call volume`), true, `day ${i}: ${tooltipText()}`);
        assert.equal(tooltipText().includes("–"), false, "no time range");
    });
    // The bucket that starts 2026-10-31T19:00Z is 1 November in Asia/Karachi, not 31 October.
    assert.equal(formatZoneDateLabel(dayBuckets[0]!.startMs, ZONE) === formatZoneDateLabel(dayBuckets[0]!.startMs, "UTC"), false);
    fireEvent.mouseMove(svg, { clientX: screenX(PLOT_LEFT), clientY: 200 });
    assert.equal(tooltipText().startsWith(new Date(Date.parse("2026-11-01T12:00:00Z")).toLocaleDateString(undefined, { timeZone: "UTC" })), true, tooltipText());
});

test("0.2: the keyboard's hover box prints the same date for a day bucket", () => {
    render(<LiveCallsTimeSeriesChart buckets={dayBuckets} timeZone={ZONE} />);
    const svg = screen.getByRole("img", { name: "Live calls time-series chart" });
    fireEvent.keyDown(svg, { key: "Home" });
    assert.equal(tooltipText().startsWith(`${formatZoneDateLabel(dayBuckets[0]!.startMs, ZONE)}Total call volume`), true, tooltipText());
});

test("0.2: an hour bucket's hover box still prints its two times", () => {
    render(<LiveCallsTimeSeriesChart buckets={hourBuckets} timeZone={ZONE} />);
    const svg = screen.getByRole("img", { name: "Live calls time-series chart" });
    fireEvent.keyDown(svg, { key: "End" });
    const newest = hourBuckets[4]!;
    assert.equal(tooltipText().startsWith(`${hhmm(newest.startMs)}–${hhmm(newest.endMs)}Total call volume`), true, tooltipText());
});
