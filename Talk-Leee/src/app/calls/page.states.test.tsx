/**
 * FX2b Task 3.1, 3.2 — Call History uses the shared states: loading is a
 * status titled "Loading call history", a failed read shows its message with
 * "Retry" (never a validation dump), both empties are the shared EmptyState
 * ("Clear filters" stays), and the row's transcript panel loads and fails in
 * the shared states.
 */
import Module from "node:module";
import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { z } from "zod";

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
const shown = (text: string | RegExp) => screen.queryByText(text) !== null;

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

test("list: loading is a status titled 'Loading call history'", async () => {
    let answer: (() => void) | null = null;
    mock.method(dashboardApi, "listCallsCursor", () => new Promise<Listing>((resolve) => { answer = () => resolve({ calls: [call], nextCursor: null } as Listing); }));
    renderWithQueryClient(<Page />);
    const status = await screen.findByRole("status");
    assert.equal(within(status).queryByText("Loading call history") !== null, true);
    await waitFor(() => assert.equal(answer !== null, true), { timeout: SLOW });
    await act(async () => { answer!(); });
    await waitFor(() => assert.equal(shown("Loading call history"), false), { timeout: SLOW });
});

test("list: a failed read shows the server's message with Retry, which reads again", async () => {
    let fail = true;
    const read = mock.method(dashboardApi, "listCallsCursor", async () => {
        if (fail) throw new Error("Call history is unavailable.");
        return { calls: [call], nextCursor: null } as Listing;
    });
    renderWithQueryClient(<Page />);
    await waitFor(() => assert.equal(shown("Call history is unavailable."), true), { timeout: SLOW });
    assert.equal(screen.getByText("Call history is unavailable.").getAttribute("role"), "alert");
    assert.equal(screen.queryByRole("button", { name: "Try again" }) === null, true);
    fail = false;
    const before = read.mock.callCount();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => assert.equal(document.querySelector("[data-call-row]") !== null, true), { timeout: SLOW });
    assert.equal(read.mock.callCount() > before, true);
});

test("list: an answer the app could not read shows 'Please retry.', never the validation dump", async () => {
    const parsed = z.object({ calls: z.array(z.string()) }).safeParse({ calls: 1 });
    assert.equal(parsed.success, false);
    mock.method(dashboardApi, "listCallsCursor", async () => { throw parsed.error; });
    renderWithQueryClient(<Page />);
    await waitFor(() => assert.equal(shown("Please retry."), true), { timeout: SLOW });
    assert.equal(shown(/invalid_type|expected array/i), false);
});

test("list: a /calls answer without its list shows 'Please retry.', never the raw JavaScript text", async () => {
    // What the client does with an answer that has no `items`.
    mock.method(dashboardApi, "listCallsCursor", async () => {
        const answer = {} as { items: Call[] };
        return { calls: answer.items.map((item) => item), nextCursor: null };
    });
    renderWithQueryClient(<Page />);
    await waitFor(() => assert.equal(shown("Please retry."), true), { timeout: SLOW });
    assert.equal(shown(/Cannot read properties/), false);
    assert.equal(screen.queryByRole("button", { name: "Retry" }) !== null, true);
});

test("list: no calls shows 'No calls yet' in the shared EmptyState", async () => {
    mock.method(dashboardApi, "listCallsCursor", async () => ({ calls: [], nextCursor: null }) as Listing);
    renderWithQueryClient(<Page />);
    const title = await screen.findByText("No calls yet", undefined, { timeout: SLOW });
    assert.equal(title.tagName, "H2", "the shared EmptyState title");
    assert.equal(shown("Calls will appear here after an outbound campaign starts or an active inbound line receives a call."), true);
});

test("list: no match shows 'No calls match your search or filter.' in the shared EmptyState and keeps 'Clear filters'", async () => {
    mock.method(dashboardApi, "listCallsCursor", async () => ({ calls: [call], nextCursor: null }) as Listing);
    renderWithQueryClient(<Page />);
    const search = await screen.findByLabelText("Search calls by phone number or campaign", undefined, { timeout: SLOW });
    fireEvent.change(search, { target: { value: "zzz-no-such" } });
    const title = await screen.findByText("No calls match your search or filter.", undefined, { timeout: SLOW });
    assert.equal(title.tagName, "H2", "the shared EmptyState title");
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => assert.equal(document.querySelector("[data-call-row]") !== null, true), { timeout: SLOW });
    assert.equal(shown("No calls match your search or filter."), false);
});

test("transcript panel: loading is a status; a failed read shows its message with Retry, which reads again", async () => {
    mock.method(dashboardApi, "listCallsCursor", async () => ({ calls: [call], nextCursor: null }) as Listing);
    let fail = true;
    let answer: (() => void) | null = null;
    type Transcript = Awaited<ReturnType<typeof dashboardApi.getCallTranscript>>;
    const read = mock.method(dashboardApi, "getCallTranscript", () => new Promise<Transcript>((resolve, reject) => {
        const settle = fail
            ? () => reject(new Error("Transcript store is down."))
            : () => resolve({ turns: [{ role: "assistant", content: "Hello there" }] } as unknown as Transcript);
        if (read.mock.callCount() === 0) answer = settle; else settle();
    }));
    renderWithQueryClient(<Page />);
    await waitFor(() => assert.equal(document.querySelector("[data-call-row]") !== null, true), { timeout: SLOW });
    fireEvent.click(screen.getAllByRole("button", { name: "Show transcript" })[0]);
    await waitFor(() => assert.equal(
        screen.queryAllByRole("status").some((node) => within(node).queryByText("Loading transcript…") !== null), true,
    ), { timeout: SLOW });
    await act(async () => { answer!(); });
    await waitFor(() => assert.equal(shown("Transcript store is down."), true), { timeout: SLOW });
    assert.equal(screen.queryByRole("button", { name: "Try again" }) === null, true);
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => assert.equal(shown("Hello there"), true), { timeout: SLOW });
    assert.equal(read.mock.callCount(), 2);
});
