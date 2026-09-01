import assert from "node:assert/strict";
import test from "node:test";

import { cleanup, render, screen } from "@testing-library/react";

import { CallSummaryCard, summaryNeedsReview } from "@/components/calls/CallSummaryCard";
import type { CallSummaryObj } from "@/lib/dashboard-api";

function summary(patch: Partial<CallSummaryObj> = {}): CallSummaryObj {
    return {
        headline: "Call recap",
        outcome: "answered",
        what_happened: "The caller asked about the service.",
        key_points: [],
        objections: [],
        commitments: [],
        action_items: [],
        sentiment: "neutral",
        next_step: "",
        notable_quotes: [],
        ...patch,
    };
}

test.afterEach(cleanup);

test("actionable AI classifications receive a visible needs-review signal", () => {
    const value = summary({ outcome: "qualified", qualification_status: "qualified" });
    assert.equal(summaryNeedsReview(value), true);

    render(<CallSummaryCard isLoading={false} isError={false} data={{ available: true, summary: value }} />);
    assert.ok(screen.getByText("Needs review"));
    assert.ok(screen.getByRole("button", { name: /why this summary needs review/i }));
});

test("a summary with no actionable classification does not invent confidence", () => {
    const value = summary();
    assert.equal(summaryNeedsReview(value), false);

    render(<CallSummaryCard isLoading={false} isError={false} data={{ available: true, summary: value }} />);
    assert.equal(screen.queryByText("Needs review"), null);
    assert.doesNotMatch(document.body.textContent ?? "", /\d+% confidence/i);
});
