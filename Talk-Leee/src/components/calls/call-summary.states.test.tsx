/**
 * FX2b Task 3.2 — the summary panel uses the shared states: loading is a
 * status titled "Generating AI summary…", a failed read shows its message
 * with "Retry" (never a validation dump).
 */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { z } from "zod";

import { CallSummaryCard } from "@/components/calls/CallSummaryCard";

afterEach(cleanup);
const shown = (text: string | RegExp) => screen.queryByText(text) !== null;

test("summary: loading is a status titled 'Generating AI summary…'", () => {
    render(<CallSummaryCard isLoading isError={false} />);
    assert.equal(within(screen.getByRole("status")).queryByText("Generating AI summary…") !== null, true);
});

test("summary: a failed read shows the server's message in the shared ErrorState with Retry, which reads again", () => {
    let reads = 0;
    render(<CallSummaryCard isLoading={false} isError error={new Error("Summary service is down.")} onRetry={() => { reads += 1; }} />);
    assert.equal(screen.getByText("Summary service is down.").getAttribute("role"), "alert");
    assert.equal(shown("Something went wrong"), true, "the shared ErrorState");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    assert.equal(reads, 1);
});

test("summary: an answer the app could not read shows 'Please retry.', never the validation dump", () => {
    const parsed = z.object({ available: z.boolean() }).safeParse({ available: "yes" });
    assert.equal(parsed.success, false);
    render(<CallSummaryCard isLoading={false} isError error={parsed.error} onRetry={() => {}} />);
    assert.equal(shown("Please retry."), true);
    assert.equal(shown(/invalid_type|expected boolean/i), false);
});

test("summary: a failure without a message keeps the panel's own sentence", () => {
    render(<CallSummaryCard isLoading={false} isError error={null} onRetry={() => {}} />);
    assert.equal(shown("Failed to load summary."), true);
});
