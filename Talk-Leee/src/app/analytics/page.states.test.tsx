/**
 * FX2b Task 5 — Analytics uses the shared states: loading is a status titled
 * "Loading analytics…", a failed read shows its message with Retry (an answer
 * without its series is a failed read; no validation dump), and the desktop
 * table, the chart and the mobile list all show "No data for the selected
 * period" in the shared EmptyState.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { z } from "zod";
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
const shown = (text: string | RegExp) => screen.queryByText(text) !== null;
type Series = Awaited<ReturnType<typeof extendedApi.getCallAnalytics>>;
const day = { date: "2026-03-01", total_calls: 4, answered: 3, failed: 1 };

test("loading is a status titled 'Loading analytics…'", async () => {
    let answer: (() => void) | null = null;
    mock.method(extendedApi, "getCallAnalytics", () => new Promise<Series>((resolve) => { answer = () => resolve({ series: [day] } as Series); }));
    render(<Page />);
    const status = await screen.findByRole("status");
    assert.equal(within(status).queryByText("Loading analytics…") !== null, true);
    assert.equal(status.querySelector(".animate-spin") === null, true, "the shared LoadingState, not the page's own spinner");
    await waitFor(() => assert.equal(answer !== null, true), { timeout: SLOW });
    await act(async () => { answer!(); });
    await waitFor(() => assert.equal(shown("Breakdown"), true), { timeout: SLOW });
});

test("a failed read shows the server's message with Retry, which reads again", async () => {
    let fail = true;
    const read = mock.method(extendedApi, "getCallAnalytics", async () => {
        if (fail) throw new Error("Analytics are unavailable.");
        return { series: [day] } as Series;
    });
    render(<Page />);
    await waitFor(() => assert.equal(shown("Analytics are unavailable."), true), { timeout: SLOW });
    assert.equal(screen.getByText("Analytics are unavailable.").getAttribute("role"), "alert");
    fail = false;
    const before = read.mock.callCount();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => assert.equal(shown("Breakdown"), true), { timeout: SLOW });
    assert.equal(read.mock.callCount(), before + 1);
    assert.equal(shown("Analytics are unavailable."), false);
});

test("an answer without its series is a failed read ('Failed to load analytics'), and a validation dump is never shown", async () => {
    mock.method(extendedApi, "getCallAnalytics", async () => ({ detail: "unexpected" }) as unknown as Series);
    render(<Page />);
    await waitFor(() => assert.equal(shown("Failed to load analytics"), true), { timeout: SLOW });
    assert.equal(screen.queryByRole("button", { name: "Retry" }) !== null, true);
    assert.equal(shown("No data for the selected period"), false, "a failed read is not 'no data'");
    cleanup();
    mock.restoreAll();

    const parsed = z.object({ series: z.array(z.string()) }).safeParse({ series: 1 });
    assert.equal(parsed.success, false);
    mock.method(extendedApi, "getCallAnalytics", async () => { throw parsed.error; });
    render(<Page />);
    await waitFor(() => assert.equal(shown("Please retry."), true), { timeout: SLOW });
    assert.equal(shown(/invalid_type|expected array/i), false);
});

async function mountEmpty() {
    mock.method(extendedApi, "getCallAnalytics", async () => ({ series: [] }) as unknown as Series);
    render(<Page />);
    await waitFor(() => assert.equal(shown("Breakdown"), true), { timeout: SLOW });
}
const emptyTitleIn = (container: Element | null) =>
    container !== null && Array.from(container.querySelectorAll("h2")).some((node) => node.textContent === "No data for the selected period");

test("the desktop table with no data shows 'No data for the selected period' in the shared EmptyState, not an empty table", async () => {
    await mountEmpty();
    const desktop = document.querySelector(".hidden.md\\:block");
    assert.equal(emptyTitleIn(desktop), true);
    assert.equal(desktop!.querySelector("table") === null, true);
});

test("the chart with no data shows 'No data for the selected period' in the shared EmptyState", async () => {
    await mountEmpty();
    const chart = screen.getByRole("heading", { name: "Call Volume" }).closest(".rounded-2xl");
    assert.equal(emptyTitleIn(chart), true);
});

test("the mobile list with no data shows 'No data for the selected period' in the shared EmptyState", async () => {
    await mountEmpty();
    assert.equal(emptyTitleIn(document.querySelector(".md\\:hidden")), true);
});

test("with data the desktop table still shows its rows", async () => {
    mock.method(extendedApi, "getCallAnalytics", async () => ({ series: [day] }) as Series);
    render(<Page />);
    await waitFor(() => assert.equal(shown("Breakdown"), true), { timeout: SLOW });
    assert.equal(document.querySelector(".hidden.md\\:block table tbody tr") !== null, true);
    assert.equal(shown("No data for the selected period"), false);
});
