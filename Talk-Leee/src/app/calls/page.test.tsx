import assert from "node:assert/strict";
import test from "node:test";

import { cleanup, render, screen } from "@testing-library/react";

import { CallsLoadError, SummaryPreview, distinctDidOptions, inboundCampaignFilterOptions } from "@/components/calls/call-panels";
import type { CallSummaryEnvelope, CallSummaryObj } from "@/lib/dashboard-api";

test.afterEach(cleanup);

const currentSummary: CallSummaryObj = {
    headline: "Current source headline", what_happened: "Current source body",
    outcome: "answered", sentiment: "neutral", key_points: ["Current source point"],
    next_step: "Current source next step", objections: [], commitments: [], action_items: [], notable_quotes: [],
};

for (const [label, data] of [
    ["unavailable", { available: false, summary: null }],
    ["missing", { available: true, summary: null }],
    ["stale", { available: true, summary: currentSummary, source_evidence: {
        transcript_save_state: "complete", summary_current: false, review_required: true, revision: "b".repeat(64),
    } }],
] as const) {
    test(`the ${label} current preview cannot fall back to a cached list headline`, () => {
        render(<SummaryPreview fallback="Cached list headline" isLoading={false} isError={false} data={data} />);
        assert.match(screen.getByRole("status").textContent ?? "", /No current summary.*saved evidence/);
        assert.equal(screen.queryByText("Cached list headline"), null);
        for (const text of [currentSummary.headline, ...currentSummary.key_points, currentSummary.next_step]) {
            assert.equal(screen.queryByText(text), null);
        }
    });
}

for (const state of ["partial", "failed", "unknown", "complete"] as const) {
    test(`the ${state} preview renders current source content with an independent source warning`, () => {
        const data: CallSummaryEnvelope = { available: true, summary: currentSummary, source_evidence: {
            transcript_save_state: state, summary_current: true, review_required: state !== "complete", revision: "a".repeat(64),
        } };
        render(<SummaryPreview fallback="Cached list headline" isLoading={false} isError={false} data={data} />);
        assert.ok(screen.getByText(currentSummary.headline));
        assert.ok(screen.getByText(currentSummary.key_points[0]));
        assert.ok(screen.getByText(currentSummary.next_step));
        assert.equal(screen.queryByText("Cached list headline"), null);
        if (state === "complete") assert.equal(screen.queryByRole("status"), null);
        else assert.match(screen.getByRole("status").textContent ?? "", {
            partial: /partial transcript/, failed: /final transcript save failed/,
            unknown: /completeness has not been verified/,
        }[state]);
    });
}

test("a failed call-history load offers a retry that re-runs the request", () => {
    let retried = 0;
    render(<CallsLoadError message="Failed to load calls" onRetry={() => { retried += 1; }} />);

    assert.ok(screen.getByRole("alert"));
    assert.ok(screen.getByText("Failed to load calls"));
    screen.getByRole("button", { name: "Try again" }).click();
    assert.equal(retried, 1);
});

test("a failed call-history load offers no retry when the caller cannot re-run it", () => {
    render(<CallsLoadError message="Failed to load calls" />);

    assert.ok(screen.getByText("Failed to load calls"));
    assert.equal(screen.queryByRole("button", { name: "Try again" }), null);
});

// ── DID and campaign filter options — client-side over the loaded page,
// exactly as `to_number`/`inbound_campaign_id` render elsewhere on this page ──

test("the DID filter lists only the DIDs the wire actually sent, deduplicated and sorted", () => {
    const options = distinctDidOptions([
        { to_number: "+18005550101" },
        { to_number: "+18005550100" },
        { to_number: "+18005550101" },
        { to_number: null },
        { to_number: undefined },
    ]);
    assert.deepEqual(options, ["+18005550100", "+18005550101"]);
});

test("an outbound call with no DID contributes nothing to the filter", () => {
    assert.deepEqual(distinctDidOptions([{ to_number: null }, { to_number: undefined }]), []);
});

test("the campaign filter is labeled by the wire's own campaign_name", () => {
    const options = inboundCampaignFilterOptions([
        { inbound_campaign_id: "cfg-1", campaign_name: "Support line" },
        { inbound_campaign_id: "cfg-1", campaign_name: "Support line" },
    ]);
    assert.deepEqual(options, [{ id: "cfg-1", name: "Support line" }]);
});

test("a campaign with no name falls back to its own id, never an invented label", () => {
    const options = inboundCampaignFilterOptions([{ inbound_campaign_id: "cfg-2" }]);
    assert.deepEqual(options, [{ id: "cfg-2", name: "cfg-2" }]);
});

test("no campaign filter option renders without the wire carrying an inbound campaign id", () => {
    const options = inboundCampaignFilterOptions([
        { inbound_campaign_id: null, campaign_name: "Outbound campaign" },
        { inbound_campaign_id: undefined },
    ]);
    assert.deepEqual(options, []);
});
