/**
 * FX4a Task 6 — the Analytics chart is one Tab stop: only its first bar is in
 * the Tab order, the arrow keys move between the bars and each bar shows the
 * chart's existing tooltip when it takes focus.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { extendedApi } from "@/lib/extended-api";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/providers/theme-provider")) return { useTheme: () => ({ theme: "light", setTheme() {} }) };
    return originalLoad.call(this, id, ...args);
};
let Page: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Page = require("./page").default;
} finally {
    loader._load = originalLoad;
}

const SLOW = 15_000;
const originalScrollIntoView = window.Element.prototype.scrollIntoView;
beforeEach(() => { window.Element.prototype.scrollIntoView = () => {}; });
afterEach(() => { cleanup(); mock.restoreAll(); window.Element.prototype.scrollIntoView = originalScrollIntoView; });
type Series = Awaited<ReturnType<typeof extendedApi.getCallAnalytics>>;
const series = [
    { date: "2026-03-01", total_calls: 4, answered: 3, failed: 1 },
    { date: "2026-03-02", total_calls: 9, answered: 7, failed: 2 },
    { date: "2026-03-03", total_calls: 6, answered: 6, failed: 0 },
];
const w = window as unknown as { requestAnimationFrame?: (cb: FrameRequestCallback) => number; cancelAnimationFrame?: (id: number) => void };
if (typeof w.requestAnimationFrame !== "function") {
    w.requestAnimationFrame = (cb) => setTimeout(() => cb(performance.now()), 0) as unknown as number;
    w.cancelAnimationFrame = (id) => clearTimeout(id);
}

const bars = () => Array.from(document.querySelectorAll<HTMLElement>("[data-chart-point]"));
/** The chart's own tooltip (it has no role; it is the one floating box over the bars). */
const tooltipText = () => Array.from(document.querySelectorAll("div")).find((box) => box.className.includes("pointer-events-none absolute z-10"))?.textContent ?? null;
const focusIsOn = (element: Element | null | undefined) => element !== null && element !== undefined && document.activeElement === element;

test("the chart is one Tab stop; the arrow keys move between its bars and show each bar's tooltip", async () => {
    mock.method(extendedApi, "getCallAnalytics", async () => ({ timezone: "UTC", series }) as Series);
    render(<Page />);
    await waitFor(() => assert.equal(bars().length, 3), { timeout: SLOW });
    assert.deepEqual(bars().map((bar) => bar.getAttribute("tabindex")), ["0", "-1", "-1"]);
    assert.equal(bars().every((bar) => (bar.getAttribute("aria-label") ?? "").includes("total")), true, "each bar keeps its own label");

    act(() => { bars()[0]!.focus(); });
    assert.equal(focusIsOn(bars()[0]), true);
    assert.equal(tooltipText()?.includes("Total: 4") && tooltipText()?.includes("Answered: 3"), true, "the first bar's tooltip is shown");
    fireEvent.keyDown(bars()[0]!, { key: "ArrowRight" });
    assert.equal(focusIsOn(bars()[1]), true, "ArrowRight");
    assert.equal(tooltipText()?.includes("Total: 9") && tooltipText()?.includes("Failed: 2"), true, "the tooltip follows the focus");
    fireEvent.keyDown(bars()[1]!, { key: "End" });
    assert.equal(focusIsOn(bars()[2]), true, "End");
    fireEvent.keyDown(bars()[2]!, { key: "ArrowRight" });
    assert.equal(focusIsOn(bars()[2]), true, "stays on the last bar");
    fireEvent.keyDown(bars()[2]!, { key: "ArrowLeft" });
    assert.equal(focusIsOn(bars()[1]), true, "ArrowLeft");
    fireEvent.keyDown(bars()[1]!, { key: "Home" });
    assert.equal(focusIsOn(bars()[0]), true, "Home");
    // Tab is left to the browser, so it leaves the chart.
    assert.equal(fireEvent.keyDown(bars()[0]!, { key: "Tab" }), true);
    act(() => { bars()[0]!.blur(); });
    assert.equal(tooltipText() === null, true, "leaving the chart hides the tooltip");
});
