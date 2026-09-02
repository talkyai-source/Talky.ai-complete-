import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React, { useState } from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import {
    CallHistoryFormButton,
    CallReviewModal,
    LeadTypeSelect,
} from "@/components/calls/call-history-workflow-controls";
import type { CallHistoryFormData, CallHistoryLeadType } from "@/lib/call-history-workflow";
import { EMPTY_CALL_HISTORY_FORM } from "@/lib/call-history-workflow";
import type { Call } from "@/lib/dashboard-api";
import { extendedApi } from "@/lib/extended-api";
import { ensureDom } from "@/test-utils/dom";

ensureDom();

const originalGetMyReview = extendedApi.getMyReview;
const originalSubmitReview = extendedApi.submitReview;

afterEach(() => {
    cleanup();
    extendedApi.getMyReview = originalGetMyReview;
    extendedApi.submitReview = originalSubmitReview;
});

test("lead dropdown exposes cold, warm, hot, and follow-up color states", async () => {
    function Harness() {
        const [value, setValue] = useState<CallHistoryLeadType>("warm");
        return <LeadTypeSelect value={value} onChange={setValue} callLabel="+15550001111" />;
    }

    render(<Harness />);
    const user = userEvent.setup({ document });
    const select = screen.getByRole("combobox", { name: /lead type for/i });

    assert.deepEqual(
        screen.getAllByRole("option").map((option) => option.textContent),
        ["Cold", "Warm", "Hot", "Follow-up"],
    );
    await user.selectOptions(select, "hot");
    assert.equal((select as HTMLSelectElement).value, "hot");
    assert.match(select.className, /emerald/);
});

test("completing the post-call form persists its values and turns its trigger green", async () => {
    function Harness() {
        const [value, setValue] = useState<CallHistoryFormData>({ ...EMPTY_CALL_HISTORY_FORM });
        return <CallHistoryFormButton value={value} onChange={setValue} callLabel="+15550001111" showLabel />;
    }

    render(<Harness />);
    const user = userEvent.setup({ document });

    await user.click(screen.getByRole("button", { name: /open form for/i }));
    await user.type(screen.getByLabelText(/contact or decision maker/i), "Ava");
    await user.type(screen.getByLabelText(/key need or interest/i), "Annual plan pricing");
    await user.type(screen.getByLabelText(/next step/i), "Send a proposal Friday");
    await user.click(screen.getByRole("button", { name: /^complete form$/i }));

    assert.ok(await screen.findByText("Form completed"));
    const completedTrigger = screen.getByRole("button", { name: /completed form for/i });
    assert.match(completedTrigger.className, /emerald/);
});

test("call review opens as a pop-up and saves a thumbs-up through the review API", async () => {
    extendedApi.getMyReview = async () => null;
    let submittedRating = 0;
    extendedApi.submitReview = async (callId, review) => {
        submittedRating = review.rating;
        return {
            id: "review-1",
            call_id: callId,
            campaign_id: null,
            user_id: "user-1",
            rating: review.rating,
            tags: review.tags,
            comment: review.comment ?? null,
            prompt_template: null,
            prompt_version: null,
            prompt_hash: null,
            awarded_points: 0,
            created_at: "2026-09-03T10:00:00.000Z",
            updated_at: "2026-09-03T10:00:00.000Z",
        };
    };
    const call: Call = {
        id: "call-1",
        campaign_id: "campaign-1",
        lead_id: "lead-1",
        phone_number: "+15550001111",
        status: "completed",
        created_at: "2026-09-03T10:00:00.000Z",
    };

    const qc = new QueryClient({
        defaultOptions: {
            queries: { retry: false, gcTime: 0 },
            mutations: { retry: false, gcTime: 0 },
        },
    });
    render(
        <QueryClientProvider client={qc}>
            <CallReviewModal call={call} onClose={() => undefined} />
        </QueryClientProvider>,
    );
    const user = userEvent.setup({ document });
    const up = await screen.findByRole("button", { name: /rate this conversation good/i });
    await user.click(up);

    await waitFor(() => assert.equal(submittedRating, 5));
    assert.ok(await screen.findByText("Review saved"));
    cleanup();
    qc.clear();
});
