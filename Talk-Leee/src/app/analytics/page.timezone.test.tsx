import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { extendedApi } from "@/lib/extended-api";

// Plan 3.7 Z3 on the Analytics page: the range menu and the daily chart say
// "Timezone: UTC" and the axis labels print the UTC date.
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
const tzBefore = process.env.TZ;
afterEach(() => {
    cleanup();
    mock.restoreAll();
    if (tzBefore === undefined) delete process.env.TZ;
    else process.env.TZ = tzBefore;
});

test("the range menu and the daily chart show 'Timezone: UTC' and print the UTC date in a zone behind UTC", async () => {
    process.env.TZ = "America/Los_Angeles";
    mock.method(extendedApi, "getCallAnalytics", async () => ({
        series: [{ date: "2026-03-01", total_calls: 4, answered: 3, failed: 1 }],
    }));
    render(<Page />);
    await waitFor(() => assert.equal(screen.queryByTestId("analytics-chart-timezone") !== null, true), { timeout: SLOW });
    assert.equal(screen.getByTestId("analytics-range-timezone").textContent, "Timezone: UTC");
    assert.equal(screen.getByTestId("analytics-chart-timezone").textContent, "Timezone: UTC");
    // The range menu's label sits in the same pill as the menu.
    assert.equal(screen.getByTestId("analytics-range-timezone").parentElement?.contains(screen.getByRole("combobox", { name: "Select date range" })), true);
    // The bucket "2026-03-01" is Mar 1 in UTC; in Los Angeles the local date would be Feb 28.
    assert.equal(screen.getAllByText("Mar 1").length >= 1, true);
    assert.equal(screen.queryByText("Feb 28") !== null, false);
    assert.equal(screen.getByLabelText(/^Mar 1: total 4/) !== null, true);
});
