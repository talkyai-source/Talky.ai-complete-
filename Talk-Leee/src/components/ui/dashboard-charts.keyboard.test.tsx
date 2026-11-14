/**
 * FX4a Task 6 — each chart is one Tab stop. The arrow keys move between its
 * points or bars and show the chart's existing tooltip for the point they land
 * on; leaving the chart hides it.
 */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import {
    DonutChart,
    Heatmap,
    LiveCallsTimeSeriesChart,
    RealTimeBarChart,
    StackedAreaChart,
    TimeSeriesLineChart,
    moveChartPointFocus,
} from "./dashboard-charts";

if (typeof globalThis.ResizeObserver !== "function") {
    globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;
}

const w = window as unknown as { requestAnimationFrame?: (cb: FrameRequestCallback) => number; cancelAnimationFrame?: (id: number) => void };
if (typeof w.requestAnimationFrame !== "function") {
    w.requestAnimationFrame = (cb) => setTimeout(() => cb(performance.now()), 0) as unknown as number;
    w.cancelAnimationFrame = (id) => clearTimeout(id);
}

afterEach(() => { cleanup(); });

/** How many elements of the chart are in the Tab order. */
const tabStops = (root: ParentNode) => root.querySelectorAll('[tabindex="0"]').length;
const tooltipText = () => screen.queryByRole("tooltip")?.textContent ?? null;
const focusIsOn = (element: Element | null | undefined) => element !== null && element !== undefined && document.activeElement === element;
function focusOn(element: Element) {
    act(() => { (element as HTMLElement).focus(); });
    assert.equal(focusIsOn(element), true, "the element took focus");
}

const rows = ["Mon", "Tue", "Wed"];
const cols = ["0–3", "3–6", "6–9", "9–12"];
const cellValues = [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12]];

test("Heatmap: one Tab stop for its twelve cells; the arrow keys move by cell and by row and show each cell's tooltip", () => {
    const { container } = render(<Heatmap rows={rows} cols={cols} cellValues={cellValues} maxValue={12} />);
    const cells = Array.from(container.querySelectorAll<HTMLElement>("[data-chart-point]"));
    assert.equal(cells.length, 12);
    assert.equal(tabStops(container), 1);
    assert.equal(cells[0]!.getAttribute("tabindex"), "0");

    focusOn(cells[0]!);
    assert.equal(tooltipText(), "Mon0–31");
    fireEvent.keyDown(cells[0]!, { key: "ArrowRight" });
    assert.equal(focusIsOn(cells[1]), true, "ArrowRight");
    assert.equal(tooltipText(), "Mon3–62");
    fireEvent.keyDown(cells[1]!, { key: "ArrowDown" });
    assert.equal(focusIsOn(cells[5]), true, "ArrowDown moves one row down");
    assert.equal(tooltipText(), "Tue3–66");
    fireEvent.keyDown(cells[5]!, { key: "ArrowLeft" });
    assert.equal(focusIsOn(cells[4]), true, "ArrowLeft");
    fireEvent.keyDown(cells[4]!, { key: "ArrowUp" });
    assert.equal(focusIsOn(cells[0]), true, "ArrowUp moves one row up");
    fireEvent.keyDown(cells[0]!, { key: "ArrowUp" });
    assert.equal(focusIsOn(cells[0]), true, "no row above the first");
    fireEvent.keyDown(cells[0]!, { key: "End" });
    assert.equal(focusIsOn(cells[11]), true, "End");
    assert.equal(tooltipText(), "Wed9–1212");
    fireEvent.keyDown(cells[11]!, { key: "ArrowDown" });
    assert.equal(focusIsOn(cells[11]), true, "no row below the last");
    fireEvent.keyDown(cells[11]!, { key: "Home" });
    assert.equal(focusIsOn(cells[0]), true, "Home");

    // Leaving the chart hides the tooltip.
    fireEvent.blur(cells[0]!);
    assert.equal(tooltipText() === null, true);
});

test("Donut: one Tab stop; the arrow keys step through its summary, Answered and Failed tooltips", () => {
    const { container } = render(<DonutChart value={30} total={40} />);
    assert.equal(tabStops(container), 1);
    const chart = container.querySelector<HTMLElement>('[tabindex="0"]')!;
    assert.equal(chart.getAttribute("aria-label"), "Answered 30 (75%), Failed 10 (25%), Total 40");
    focusOn(chart);
    const summary = tooltipText();
    assert.equal(summary !== null, true, "focus shows the summary tooltip");
    fireEvent.keyDown(chart, { key: "ArrowRight" });
    assert.equal(tooltipText(), "Answered3075%");
    fireEvent.keyDown(chart, { key: "ArrowRight" });
    assert.equal(tooltipText(), "Failed1025%");
    fireEvent.keyDown(chart, { key: "ArrowRight" });
    assert.equal(tooltipText(), "Failed1025%", "stays on the last");
    fireEvent.keyDown(chart, { key: "ArrowLeft" });
    assert.equal(tooltipText(), "Answered3075%");
    fireEvent.keyDown(chart, { key: "ArrowLeft" });
    assert.equal(tooltipText(), summary);
    fireEvent.blur(chart);
    assert.equal(tooltipText() === null, true);
});

const dual = [{ label: "Mon", a: 3, b: 1 }, { label: "Tue", a: 5, b: 0 }, { label: "Wed", a: 2, b: 2 }];

test("Stacked area chart: one Tab stop; the arrow keys move between its points and show each point's tooltip", () => {
    const { container } = render(<StackedAreaChart points={dual} />);
    assert.equal(tabStops(container), 1);
    const chart = container.querySelector<SVGSVGElement>('svg[tabindex="0"]')!;
    assert.equal(chart.getAttribute("aria-label"), "Stacked area chart of answered and failed calls");
    focusOn(chart);
    assert.equal(tooltipText()?.startsWith("Wed"), true, "focus starts on the latest point");
    fireEvent.keyDown(chart, { key: "ArrowLeft" });
    assert.equal(tooltipText()?.startsWith("Tue"), true);
    assert.equal(tooltipText()?.includes("Total: 5"), true);
    fireEvent.keyDown(chart, { key: "Home" });
    assert.equal(tooltipText()?.startsWith("Mon"), true);
    fireEvent.keyDown(chart, { key: "ArrowLeft" });
    assert.equal(tooltipText()?.startsWith("Mon"), true, "stays on the first");
    fireEvent.keyDown(chart, { key: "End" });
    assert.equal(tooltipText()?.startsWith("Wed"), true);
    fireEvent.blur(chart);
    assert.equal(tooltipText() === null, true);
});

const buckets = [
    { startMs: Date.parse("2026-11-01T10:00:00Z"), endMs: Date.parse("2026-11-01T11:00:00Z"), total: 2, answered: 2, failed: 0, avgDurationSec: 60 },
    { startMs: Date.parse("2026-11-01T11:00:00Z"), endMs: Date.parse("2026-11-01T12:00:00Z"), total: 7, answered: 4, failed: 3, avgDurationSec: 60 },
    { startMs: Date.parse("2026-11-01T12:00:00Z"), endMs: Date.parse("2026-11-01T13:00:00Z"), total: 5, answered: 5, failed: 0, avgDurationSec: 60 },
];

test("Live calls chart: one Tab stop; the arrow keys move between its buckets and tell the page which one is active", () => {
    const active: Array<number | null> = [];
    const { container } = render(
        <LiveCallsTimeSeriesChart buckets={buckets} timeZone="UTC" onActiveBucketChange={(bucket) => active.push(bucket ? bucket.total ?? null : null)} />,
    );
    assert.equal(tabStops(container), 1);
    const chart = container.querySelector<SVGSVGElement>('svg[tabindex="0"]')!;
    assert.equal(chart.getAttribute("aria-label"), "Live calls time-series chart");
    focusOn(chart);
    assert.equal(tooltipText() !== null, true, "focus shows the latest bucket's tooltip");
    assert.equal(active.at(-1), 5);
    const latest = tooltipText();
    fireEvent.keyDown(chart, { key: "ArrowLeft" });
    assert.equal(active.at(-1), 7);
    assert.equal(tooltipText() !== latest, true, "the tooltip is the other bucket's");
    fireEvent.keyDown(chart, { key: "Home" });
    assert.equal(active.at(-1), 2);
    fireEvent.keyDown(chart, { key: "ArrowRight" });
    assert.equal(active.at(-1), 7);
    fireEvent.keyDown(chart, { key: "End" });
    assert.equal(active.at(-1), 5);
    assert.equal(tooltipText(), latest);
    fireEvent.blur(chart);
    assert.equal(tooltipText() === null, true);
    assert.equal(active.at(-1), null);
});

test("Line chart and bar chart: one Tab stop each; the arrow keys move between points and bars", () => {
    const line = render(<TimeSeriesLineChart points={[{ label: "Mon", value: 4 }, { label: "Tue", value: 9 }, { label: "Wed", value: 1 }]} />);
    const points = Array.from(line.container.querySelectorAll<SVGElement>("[data-chart-point]"));
    assert.equal(points.length, 3);
    assert.equal(tabStops(line.container), 1);
    focusOn(points[0]!);
    assert.equal(tooltipText(), "Mon4");
    fireEvent.keyDown(points[0]!, { key: "ArrowRight" });
    assert.equal(focusIsOn(points[1]), true);
    assert.equal(tooltipText(), "Tue9");
    fireEvent.keyDown(points[1]!, { key: "ArrowLeft" });
    assert.equal(focusIsOn(points[0]), true);
    cleanup();

    const bars = render(<RealTimeBarChart points={[{ label: "Now", a: 6, b: 2 }]} maxValue={10} />);
    const marks = Array.from(bars.container.querySelectorAll<SVGElement>("[data-chart-point]"));
    assert.deepEqual(marks.map((mark) => mark.getAttribute("aria-label")?.split(":")[0]), ["Answered Calls", "Failed Calls"]);
    assert.equal(tabStops(bars.container), 1);
    focusOn(marks[0]!);
    fireEvent.keyDown(marks[0]!, { key: "ArrowRight" });
    assert.equal(focusIsOn(marks[1]), true);
    fireEvent.keyDown(marks[1]!, { key: "ArrowRight" });
    assert.equal(focusIsOn(marks[1]), true, "stays on the last bar");
});

test("the shared helper leaves other keys alone, so Tab still leaves the chart", () => {
    const { container } = render(<Heatmap rows={rows} cols={cols} cellValues={cellValues} maxValue={12} />);
    const cells = Array.from(container.querySelectorAll<HTMLElement>("[data-chart-point]"));
    focusOn(cells[0]!);
    const tab = fireEvent.keyDown(cells[0]!, { key: "Tab" });
    assert.equal(tab, true, "Tab is not cancelled");
    assert.equal(focusIsOn(cells[0]), true, "and the helper moved nothing");
    const arrow = fireEvent.keyDown(cells[0]!, { key: "ArrowRight" });
    assert.equal(arrow, false, "an arrow key is handled (the page does not scroll)");
    assert.equal(typeof moveChartPointFocus, "function");
});
