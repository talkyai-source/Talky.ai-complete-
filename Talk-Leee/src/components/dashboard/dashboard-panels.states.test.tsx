/**
 * FX2b Task 1.3, 1.4 — Dialer insights loads in the shared LoadingState
 * ("Loading…") and shows "Not enough data yet" in the shared EmptyState; the
 * call-issue banner shows a failed read (server message + Retry) instead of
 * nothing, and an answer without the issue list is a failed read.
 */
import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { z } from "zod";

import { CallIssuesBanner } from "@/components/calls/call-issues-banner";
import { DialerInsights } from "@/components/dashboard/dialer-insights";
import { extendedApi } from "@/lib/extended-api";

const SLOW = 15_000;
afterEach(() => { cleanup(); mock.restoreAll(); });
const shown = (text: string | RegExp) => screen.queryByText(text) !== null;
type Issues = Awaited<ReturnType<typeof extendedApi.getRecentCallIssues>>;
type BestTime = Awaited<ReturnType<typeof extendedApi.getBestTimeToCall>>;

test("Dialer insights: loading is a status titled 'Loading…'", async () => {
    let answer: (() => void) | null = null;
    mock.method(extendedApi, "getBestTimeToCall", () => new Promise<BestTime>((resolve) => {
        answer = () => resolve({ timezone: "UTC", best_hour: null, hours: [] });
    }));
    mock.method(extendedApi, "getRetryEffectiveness", async () => ({ attempts: [] }));
    render(<DialerInsights />);
    const status = await screen.findByRole("status");
    assert.equal(within(status).queryByText("Loading…") !== null, true);
    await act(async () => { answer!(); });
    await waitFor(() => assert.equal(screen.queryByRole("status") === null, true), { timeout: SLOW });
});

test("Dialer insights: no best hour shows 'Not enough data yet' in the shared EmptyState", async () => {
    mock.method(extendedApi, "getBestTimeToCall", async () => ({ timezone: "UTC", best_hour: null, hours: [] }));
    mock.method(extendedApi, "getRetryEffectiveness", async () => ({ attempts: [] }));
    render(<DialerInsights />);
    const text = await screen.findByText("Not enough data yet", undefined, { timeout: SLOW });
    assert.equal(text.tagName, "H2", "the shared EmptyState title");
});

test("call-issue banner: a failed read shows the server's message with Retry, which reads again", async () => {
    let fail = true;
    const read = mock.method(extendedApi, "getRecentCallIssues", async (): Promise<Issues> => {
        if (fail) throw new Error("Events are unavailable.");
        return { items: [{ id: "i1", title: "Text-to-speech out of credits" }] };
    });
    render(<CallIssuesBanner />);
    await waitFor(() => assert.equal(shown("Events are unavailable."), true), { timeout: SLOW });
    assert.equal(screen.getByText("Events are unavailable.").getAttribute("role"), "alert");
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => assert.equal(shown("Text-to-speech out of credits"), true), { timeout: SLOW });
    assert.equal(read.mock.callCount(), 2);
    assert.equal(shown("Events are unavailable."), false);
});

test("call-issue banner: an answer without the issue list is a failed read ('Please retry.'), and a validation dump is never shown", async () => {
    mock.method(extendedApi, "getRecentCallIssues", async () => ({ detail: "unexpected" }) as unknown as Issues);
    render(<CallIssuesBanner />);
    await waitFor(() => assert.equal(shown("Please retry."), true), { timeout: SLOW });
    assert.equal(screen.queryByRole("button", { name: "Retry" }) !== null, true);
    cleanup();

    const dump = z.object({ items: z.array(z.string()) }).safeParse({ items: 1 });
    assert.equal(dump.success, false);
    mock.restoreAll();
    mock.method(extendedApi, "getRecentCallIssues", async () => { throw dump.error; });
    render(<CallIssuesBanner />);
    await waitFor(() => assert.equal(shown("Please retry."), true), { timeout: SLOW });
    assert.equal(shown(/invalid_type|expected array/i), false);
});

test("call-issue banner: no issues still renders nothing", async () => {
    const read = mock.method(extendedApi, "getRecentCallIssues", async (): Promise<Issues> => ({ items: [] }));
    const { container } = render(<CallIssuesBanner />);
    await waitFor(() => assert.equal(read.mock.callCount(), 1), { timeout: SLOW });
    await act(async () => { await Promise.resolve(); });
    assert.equal(container.childElementCount, 0);
});
