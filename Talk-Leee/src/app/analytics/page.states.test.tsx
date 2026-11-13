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
import { ApiClientError } from "@/lib/http-client";

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
    // The first request (which zone the account reports in) is held; the dated one after it answers at once.
    mock.method(extendedApi, "getCallAnalytics", () => new Promise<Series>((resolve) => {
        const send = () => resolve({ timezone: "UTC", series: [day] } as Series);
        if (answer === null) answer = send;
        else send();
    }));
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
        return { timezone: "UTC", series: [day] } as Series;
    });
    render(<Page />);
    await waitFor(() => assert.equal(shown("Could not load analytics."), true), { timeout: SLOW });
    assert.equal(screen.getByText("Could not load analytics.").getAttribute("role"), "alert");
    fail = false;
    const before = read.mock.callCount();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => assert.equal(shown("Breakdown"), true), { timeout: SLOW });
    // After a failed first read the zone is still unknown: one request asks for it, one reads the range.
    assert.equal(read.mock.callCount(), before + 2);
    assert.equal(shown("Could not load analytics."), false);
});

test("an answer without its series is a failed read ('Failed to load analytics'), and a validation dump is never shown", async () => {
    mock.method(extendedApi, "getCallAnalytics", async () => ({ detail: "unexpected" }) as unknown as Series);
    render(<Page />);
    await waitFor(() => assert.equal(shown("Could not load analytics."), true), { timeout: SLOW });
    assert.equal(screen.queryByRole("button", { name: "Retry" }) !== null, true);
    assert.equal(shown("No data for the selected period"), false, "a failed read is not 'no data'");
    cleanup();
    mock.restoreAll();

    const parsed = z.object({ series: z.array(z.string()) }).safeParse({ series: 1 });
    assert.equal(parsed.success, false);
    mock.method(extendedApi, "getCallAnalytics", async () => { throw parsed.error; });
    render(<Page />);
    await waitFor(() => assert.equal(shown("Could not load analytics."), true), { timeout: SLOW });
    assert.equal(shown(/invalid_type|expected array/i), false);
});

async function mountEmpty() {
    mock.method(extendedApi, "getCallAnalytics", async () => ({ timezone: "UTC", series: [] }) as unknown as Series);
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
    mock.method(extendedApi, "getCallAnalytics", async () => ({ timezone: "UTC", series: [day] }) as Series);
    render(<Page />);
    await waitFor(() => assert.equal(shown("Breakdown"), true), { timeout: SLOW });
    assert.equal(document.querySelector(".hidden.md\\:block table tbody tr") !== null, true);
    assert.equal(shown("No data for the selected period"), false);
});

// MERGE2 Task 2: Analytics uses the shared ErrorState with the request reference.
test("a failed read with a request reference shows it under their sentence, with Retry and no 'Try again'", async () => {
    mock.method(extendedApi, "getCallAnalytics", async () => {
        throw new ApiClientError({ status: 500, code: "internal_error", message: "boom", requestId: "req-analytics-1", url: "http://example.test/analytics/calls", method: "GET" });
    });
    render(<Page />);
    await waitFor(() => assert.equal(shown("Could not load analytics."), true), { timeout: SLOW });
    assert.equal(shown("Something went wrong"), true, "the shared ErrorState");
    assert.equal(shown("Request reference: req-analytics-1"), true);
    assert.equal(shown("boom"), false);
    assert.equal(screen.queryByRole("button", { name: "Retry" }) !== null, true);
    assert.equal(screen.queryByRole("button", { name: "Try again" }), null);
});
