/** FX1b Task 4 — "Last 7 / 30 / 90 days" ask for exactly 7, 30 and 90 UTC dates. */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
const DAY_MS = 24 * 60 * 60 * 1000;
const originalScrollIntoView = window.Element.prototype.scrollIntoView;

beforeEach(() => {
    window.Element.prototype.scrollIntoView = () => {};
});

afterEach(() => {
    cleanup();
    mock.restoreAll();
    window.Element.prototype.scrollIntoView = originalScrollIntoView;
});

/** Number of UTC dates from `from` to `to`, both included. */
function utcDatesIncluded(from: string, to: string) {
    return (Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / DAY_MS + 1;
}

test("each range asks for exactly as many UTC dates as its label says, ending today", async () => {
    const user = userEvent.setup({ document: globalThis.document });
    const asked: Array<{ from: string; to: string }> = [];
    mock.method(extendedApi, "getCallAnalytics", async (from: string, to: string) => {
        // The first request carries no dates: it only asks the server which zone the account reports in.
        if (from !== undefined) asked.push({ from, to });
        return { timezone: "UTC", series: [{ date: to, total_calls: 4, answered: 3, failed: 1 }] };
    });

    render(<Page />);
    await waitFor(() => assert.equal(asked.length >= 1, true), { timeout: SLOW });
    const today = new Date().toISOString().split("T")[0];
    assert.equal(asked.at(-1)!.to, today);
    assert.equal(utcDatesIncluded(asked.at(-1)!.from, asked.at(-1)!.to), 30);

    for (const [label, days] of [["Last 7 days", 7], ["Last 90 days", 90]] as const) {
        const before = asked.length;
        await user.click(screen.getByRole("combobox", { name: "Select date range" }));
        await user.click(within(screen.getByRole("listbox")).getByRole("option", { name: label }));
        await waitFor(() => assert.equal(asked.length > before, true), { timeout: SLOW });
        assert.equal(asked.at(-1)!.to, today);
        assert.equal(utcDatesIncluded(asked.at(-1)!.from, asked.at(-1)!.to), days);
    }
});
