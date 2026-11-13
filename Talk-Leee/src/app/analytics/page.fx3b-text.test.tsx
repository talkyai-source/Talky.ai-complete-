/**
 * FX3b Task 4.2 — the Breakdown table and the mobile list print each day in
 * the same date form as the chart (formatUtcDateLabel), not the raw ISO day.
 * Same boundary stubs as page.states.test.tsx.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { extendedApi } from "@/lib/extended-api";
import { formatUtcDateLabel } from "@/lib/utc-date-label";

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
const ISO_DAY = "2026-11-01";

test("FX3b 4.2: the Breakdown table and the mobile list print the day in the chart's date form", async () => {
    mock.method(extendedApi, "getCallAnalytics", async () => ({ series: [{ date: ISO_DAY, total_calls: 4, answered: 3, failed: 1 }] } as Series));
    render(<Page />);
    await waitFor(() => assert.equal(screen.queryByText("Breakdown") !== null, true), { timeout: SLOW });
    const label = formatUtcDateLabel(ISO_DAY);
    assert.notEqual(label, ISO_DAY);
    const cells = Array.from(document.querySelectorAll("td")).map((node) => node.textContent);
    assert.equal(cells.includes(label), true, cells.join(" | "));
    assert.equal(cells.includes(ISO_DAY), false);
    const mobile = Array.from(document.querySelectorAll("span.break-words")).map((node) => node.textContent);
    assert.equal(mobile.includes(label), true, mobile.join(" | "));
    assert.equal(mobile.includes(ISO_DAY), false);
    assert.equal(screen.queryAllByText(ISO_DAY).length, 0);
});
