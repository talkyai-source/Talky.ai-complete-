/**
 * FX2b Task 4 — Recordings uses the shared states: the first load is a
 * status; on a page change the current list stays visible; a failed read,
 * a failed permission check and the permission refusal show the shared
 * ErrorState with Retry; "No recordings yet" is the shared EmptyState.
 */
import Module from "node:module";
import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { z } from "zod";

import { extendedApi } from "@/lib/extended-api";

type Q = { isSuccess?: boolean; isLoading?: boolean; isPending?: boolean; isError?: boolean; data?: { permissions: string[] }; refetch?: () => void };
let permissions: Q = {};
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/recordings/recording-media-controls")) return { RecordingMediaControls: nothing };
    if (id.includes("components/calls/recording-feedback-bar")) return { RecordingFeedbackBar: nothing };
    if (id.includes("lib/queries/inbound-queries")) return { useEffectivePermissions: () => permissions };
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
afterEach(() => { cleanup(); mock.restoreAll(); permissions = {}; });
const shown = (text: string | RegExp) => screen.queryByText(text) !== null;
const canRead = (refetch: () => void = () => {}): Q => ({ isSuccess: true, data: { permissions: ["recordings:read"] }, refetch });
type Listing = Awaited<ReturnType<typeof extendedApi.listRecordings>>;
const recording = (n: number) => ({
    id: `rec-${n}`, call_id: `call-0000000${n}`, phone_number: `+44740012300${n}`, created_at: "2026-01-01T00:00:00Z",
    duration_seconds: 65, file_size_bytes: 1000, status: "ready", legal_hold: false,
});
const listing = (n: number, total = 1) => ({ items: [recording(n)], total, page: 1, page_size: 20 }) as unknown as Listing;

test("the first load is a status (the shared LoadingState)", async () => {
    permissions = canRead();
    let answer: (() => void) | null = null;
    mock.method(extendedApi, "listRecordings", () => new Promise<Listing>((resolve) => { answer = () => resolve(listing(1)); }));
    render(<Page />);
    assert.equal(screen.queryByRole("status") !== null, true);
    assert.equal(shown("Loading"), true);
    assert.equal(shown("No recordings yet"), false, "loading is never shown as empty");
    await waitFor(() => assert.equal(answer !== null, true), { timeout: SLOW });
    await act(async () => { answer!(); });
    await waitFor(() => assert.equal(shown("+447400123001"), true), { timeout: SLOW });
    assert.equal(screen.queryByRole("status") === null, true);
});

test("on a page change the current list stays visible while the next page loads", async () => {
    permissions = canRead();
    let answerSecond: (() => void) | null = null;
    mock.method(extendedApi, "listRecordings", (_call: string | undefined, page: number = 1) => (
        page === 1
            ? Promise.resolve(listing(1, 40))
            : new Promise<Listing>((resolve) => { answerSecond = () => resolve(listing(2, 40)); })
    ));
    render(<Page />);
    await waitFor(() => assert.equal(shown("+447400123001"), true), { timeout: SLOW });
    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    await waitFor(() => assert.equal(answerSecond !== null, true), { timeout: SLOW });
    assert.equal(shown("+447400123001"), true, "the current list is still on screen");
    assert.equal(screen.queryByRole("status") === null, true, "no loading state replaces the list");
    await act(async () => { answerSecond!(); });
    await waitFor(() => assert.equal(shown("+447400123002"), true), { timeout: SLOW });
    assert.equal(shown("+447400123001"), false);
});

test("a failed read shows the server's message with Retry, which reads again", async () => {
    permissions = canRead();
    let fail = true;
    const read = mock.method(extendedApi, "listRecordings", async () => {
        if (fail) throw new Error("Recordings are unavailable.");
        return listing(1);
    });
    render(<Page />);
    await waitFor(() => assert.equal(shown("Recordings are unavailable."), true), { timeout: SLOW });
    assert.equal(screen.getByText("Recordings are unavailable.").getAttribute("role"), "alert");
    fail = false;
    const before = read.mock.callCount();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => assert.equal(shown("+447400123001"), true), { timeout: SLOW });
    assert.equal(read.mock.callCount(), before + 1);
});

test("an unexpected answer is a failed read: 'Failed to load recordings', never 'No recordings yet' or a validation dump", async () => {
    permissions = canRead();
    mock.method(extendedApi, "listRecordings", async () => ({ total: 0 }) as unknown as Listing);
    render(<Page />);
    await waitFor(() => assert.equal(shown("Failed to load recordings"), true), { timeout: SLOW });
    assert.equal(shown("No recordings yet"), false);
    cleanup();
    mock.restoreAll();

    const parsed = z.object({ items: z.array(z.string()) }).safeParse({ items: 1 });
    assert.equal(parsed.success, false);
    mock.method(extendedApi, "listRecordings", async () => { throw parsed.error; });
    render(<Page />);
    await waitFor(() => assert.equal(shown("Please retry."), true), { timeout: SLOW });
    assert.equal(shown(/invalid_type|expected array/i), false);
});

test("the permission refusal shows its sentence in the shared ErrorState with Retry, which checks again", () => {
    let checks = 0;
    permissions = { isSuccess: true, data: { permissions: [] }, refetch: () => { checks += 1; } };
    render(<Page />);
    assert.equal(screen.getByText("You do not have permission to view or play call recordings.").getAttribute("role"), "alert");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    assert.equal(checks, 1);
});

test("a failed permission check shows its sentence with Retry, which checks again", () => {
    let checks = 0;
    permissions = { isError: true, refetch: () => { checks += 1; } };
    render(<Page />);
    assert.equal(screen.getByText("Recording permissions could not be verified. Please refresh and try again.").getAttribute("role"), "alert");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    assert.equal(checks, 1);
});

test("no recordings shows 'No recordings yet' in the shared EmptyState", async () => {
    permissions = canRead();
    mock.method(extendedApi, "listRecordings", async () => ({ items: [], total: 0, page: 1, page_size: 20 }) as unknown as Listing);
    render(<Page />);
    const title = await screen.findByText("No recordings yet", undefined, { timeout: SLOW });
    assert.equal(title.closest(".content-card") === null, true, "the shared EmptyState, not the page's own card");
    assert.equal(shown("Recordings will appear here after calls are completed."), true);
});
