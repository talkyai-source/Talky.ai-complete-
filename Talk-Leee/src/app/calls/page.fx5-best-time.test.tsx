/**
 * FX5 Task B5 — "Best time to call": the button and its panel are shown only
 * for a call that carries the field.
 */
import Module from "node:module";
import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";

import { dashboardApi, type Call } from "@/lib/dashboard-api";
import { renderWithQueryClient } from "@/test-utils/render";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/calls/call-issues-banner")) return { CallIssuesBanner: nothing };
    if (id.includes("components/calls/dial-again-button")) return { DialAgainButton: nothing };
    if (id.includes("lib/queries/inbound-queries")) return { useEffectivePermissions: () => ({ isSuccess: true, isError: false, data: { permissions: [] } }) };
    if (id.endsWith("lib/auth-context")) return { useAuth: () => ({ user: { id: "user-a", tenant_id: "tenant-a" } }) };
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
const call = {
    id: "call-1", campaign_id: "camp-1", campaign_name: "Main campaign", lead_id: "", phone_number: "+447400000001",
    status: "completed", duration_seconds: 30, created_at: "2026-01-01T00:00:00Z", direction: "outbound", to_number: "+447400000001",
} as Call;
type Listing = Awaited<ReturnType<typeof dashboardApi.listCallsCursor>>;

const originalRequestAnimationFrame = window.requestAnimationFrame;
const originalCancelAnimationFrame = window.cancelAnimationFrame;
const originalPause = window.HTMLMediaElement.prototype.pause;
const originalScrollIntoView = window.Element.prototype.scrollIntoView;
beforeEach(() => {
    window.history.replaceState(null, "", "/calls");
    window.requestAnimationFrame = (callback) => window.setTimeout(() => callback(performance.now()), 0);
    window.cancelAnimationFrame = (handle) => window.clearTimeout(handle);
    window.HTMLMediaElement.prototype.pause = () => {};
    window.Element.prototype.scrollIntoView = () => {};
});
afterEach(() => {
    cleanup();
    mock.restoreAll();
    window.requestAnimationFrame = originalRequestAnimationFrame;
    window.cancelAnimationFrame = originalCancelAnimationFrame;
    window.HTMLMediaElement.prototype.pause = originalPause;
    window.Element.prototype.scrollIntoView = originalScrollIntoView;
    window.history.replaceState(null, "", "/");
});

const rowShown = () => waitFor(() => assert.equal(document.querySelector("[data-call-row]") !== null, true), { timeout: SLOW });
const bestTimeButton = () => screen.queryByRole("button", { name: /best time to call/i });

test("a call without the field offers no 'Best time to call' button and no panel", async () => {
    mock.method(dashboardApi, "listCallsCursor", async () => ({ calls: [call], nextCursor: null }) as Listing);
    renderWithQueryClient(<Page />);
    await rowShown();
    assert.equal(bestTimeButton() === null, true);
    assert.equal(screen.queryByText(/Best time to call/i) === null, true);
    assert.equal(screen.queryByText("Best time to call is not available for this call yet.") === null, true);
    assert.equal(screen.queryAllByRole("button", { name: /transcript/i }).length > 0, true, "the row's other buttons stay");
});

test("a call that carries the field offers the button, and its panel shows the server's value", async () => {
    const withField = { ...call, best_time_to_call: "Weekdays 10:00 to 12:00" } as Call;
    mock.method(dashboardApi, "listCallsCursor", async () => ({ calls: [withField], nextCursor: null }) as Listing);
    renderWithQueryClient(<Page />);
    await rowShown();
    const button = bestTimeButton();
    assert.equal(button !== null, true);
    assert.equal(screen.queryByText("Weekdays 10:00 to 12:00") === null, true, "closed until asked for");
    fireEvent.click(button!);
    await waitFor(() => assert.equal(screen.queryByText("Weekdays 10:00 to 12:00") !== null, true), { timeout: SLOW });
    assert.equal(screen.queryByText("Best time to call is not available for this call yet.") === null, true);
});
