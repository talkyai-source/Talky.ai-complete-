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

test("a failed summary offers a retry that re-runs the request", async () => {
    let retried = 0;
    render(<CallSummaryCard isLoading={false} isError error={new Error("upstream timed out")} onRetry={() => { retried += 1; }} />);

    assert.ok(screen.getByText("upstream timed out"));
    const button = screen.getByRole("button", { name: "Retry" });
    button.click();
    assert.equal(retried, 1);
});

test("a failed summary offers no retry when the caller cannot re-run it", () => {
    render(<CallSummaryCard isLoading={false} isError error={new Error("upstream timed out")} />);

    assert.ok(screen.getByText("upstream timed out"));
    assert.equal(screen.queryByRole("button", { name: "Retry" }), null);
});

for (const state of ["partial", "failed", "unknown", "complete"] as const) {
    test(`the summary shows its ${state} durable source independently of analysis`, () => {
        render(<CallSummaryCard isLoading={false} isError={false} data={{
            available: true, summary: summary(), source_evidence: {
                transcript_save_state: state, summary_current: true,
                review_required: state !== "complete", revision: "a".repeat(64),
            },
        }} />);
        assert.ok(screen.getByText("The caller asked about the service."));
        if (state === "complete") assert.equal(screen.queryByRole("status"), null);
        else assert.match(screen.getByRole("status").textContent ?? "", {
            partial: /partial transcript/, failed: /final transcript save failed/,
            unknown: /completeness has not been verified/,
        }[state]);
    });
}

test("an unavailable summary offers review and explicit reload without claiming no conversation", () => {
    let reloaded = 0;
    render(<CallSummaryCard isLoading={false} isError={false} onRetry={() => { reloaded += 1; }} data={{
        available: false, summary: null, source_evidence: {
            transcript_save_state: "partial", summary_current: false, review_required: true, revision: null,
        },
    }} />);
    assert.match(screen.getByRole("status").textContent ?? "", /saved evidence/);
    assert.doesNotMatch(document.body.textContent ?? "", /no conversation/);
    screen.getByRole("button", { name: "Reload summary" }).click();
    assert.equal(reloaded, 1);
});

test("a contradictory stale envelope cannot display its old summary", () => {
    render(<CallSummaryCard isLoading={false} isError={false} data={{
        available: true, summary: summary({ what_happened: "Obsolete result" }), source_evidence: {
            transcript_save_state: "complete", summary_current: false, review_required: true, revision: "a".repeat(64),
        },
    }} />);
    assert.equal(screen.queryByText("Obsolete result"), null);
    assert.ok(screen.getByRole("status"));
});
