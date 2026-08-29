import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";

import { ConversationReviewPanel } from "@/components/calls/conversation-review-panel";
import { extendedApi } from "@/lib/extended-api";
import { inboundQueryKeys } from "@/lib/queries/inbound-queries";
import { ensureDom } from "@/test-utils/dom";

ensureDom();

const originalOptions = extendedApi.getReviewOptions;
const originalMine = extendedApi.getMyReview;
const originalList = extendedApi.listCallReviews;
const originalSubmit = extendedApi.submitReview;

afterEach(() => {
    cleanup();
    extendedApi.getReviewOptions = originalOptions;
    extendedApi.getMyReview = originalMine;
    extendedApi.listCallReviews = originalList;
    extendedApi.submitReview = originalSubmit;
});

function stubReads() {
    extendedApi.getReviewOptions = async () => ({
        tags: ["good_conversation", "response_too_long"],
        rewards_enabled: false,
        points_per_review: 0,
        daily_cap: 0,
        bare_rating_earns_reward: false,
    });
    // The read SUCCEEDS for a readonly account — that is the whole point. The
    // backend gates it on calls:read, which such an account holds, so nothing in
    // the read path ever reveals that the submit will be refused.
    extendedApi.getMyReview = async () => null;
    extendedApi.listCallReviews = async () => [];
}

function renderPanel(permissions: string[]) {
    const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    queryClient.setQueryData(inboundQueryKeys.permissions, { permissions });
    return render(
        <QueryClientProvider client={queryClient}>
            <ConversationReviewPanel callId="call-1" />
        </QueryClientProvider>,
    );
}

// ── the gate ────────────────────────────────────────────────────────────────

test("a read-only account is told it cannot review instead of being shown the form", async () => {
    stubReads();
    let submitted = 0;
    extendedApi.submitReview = async () => {
        submitted += 1;
        throw Object.assign(new Error("Forbidden"), { status: 403 });
    };

    renderPanel(["calls:read", "recordings:read"]);

    await screen.findByText(/don't have permission to review calls/i);
    // No rating control and no submit button: there is nothing to type into and
    // therefore nothing to lose when the refusal arrives.
    assert.equal(screen.queryByRole("radiogroup", { name: /overall rating/i }), null);
    assert.equal(screen.queryByRole("button", { name: /submit review/i }), null);
    assert.equal(submitted, 0);
});

test("calls:create renders the review form", async () => {
    stubReads();
    renderPanel(["calls:read", "calls:create"]);

    assert.ok(await screen.findByRole("button", { name: /submit review/i }));
    assert.ok(screen.getByRole("radiogroup", { name: /overall rating/i }));
});

test("a failed permission lookup is reported as unchecked, not as a refusal", async () => {
    stubReads();
    const previousFetch = globalThis.fetch;
    globalThis.fetch = (async () => {
        throw new Error("network down");
    }) as typeof globalThis.fetch;

    try {
        const queryClient = new QueryClient({
            defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
        });
        // Nothing seeded, and the lookup fails → source "unavailable". The
        // wording has to distinguish this from a refusal: telling someone they
        // lack a permission they may well hold is its own kind of wrong answer.
        render(
            <QueryClientProvider client={queryClient}>
                <ConversationReviewPanel callId="call-1" />
            </QueryClientProvider>,
        );

        await screen.findByText(/permissions couldn't be checked/i);
        assert.equal(screen.queryByRole("button", { name: /submit review/i }), null);
    } finally {
        globalThis.fetch = previousFetch;
    }
});

test("teammates' reviews stay visible to an account that cannot write one", async () => {
    stubReads();
    extendedApi.listCallReviews = async () => [
        {
            id: "r-1",
            call_id: "call-1",
            campaign_id: null,
            user_id: "u-2",
            rating: 4,
            tags: ["good_conversation"],
            comment: "handled the objection well",
            prompt_template: null,
            prompt_version: null,
            prompt_hash: null,
            awarded_points: 0,
            created_at: "2026-08-01T10:00:00Z",
            updated_at: "2026-08-01T10:00:00Z",
        },
    ];

    renderPanel(["calls:read"]);

    await screen.findByText(/don't have permission to review calls/i);
    assert.ok(screen.getByText(/handled the objection well/i));
});

// ── the retry ───────────────────────────────────────────────────────────────

test("a 403 on submit does not offer a Try again that would be refused identically", async () => {
    stubReads();
    let submitted = 0;
    extendedApi.submitReview = async () => {
        submitted += 1;
        throw Object.assign(new Error("Forbidden"), { status: 403 });
    };

    renderPanel(["calls:read", "calls:create"]);
    const user = userEvent.setup({ document });

    await user.click(await screen.findByRole("radio", { name: "4 out of 5" }));
    await user.click(screen.getByRole("button", { name: /submit review/i }));

    await waitFor(() => assert.equal(submitted, 1));
    await screen.findByText(/not allowed to review calls, so retrying will not help/i);
    assert.equal(screen.queryByRole("button", { name: /try again/i }), null);
});

test("a server fault still offers Try again, and it re-sends", async () => {
    stubReads();
    let submitted = 0;
    extendedApi.submitReview = async () => {
        submitted += 1;
        throw Object.assign(new Error("Service unavailable"), { status: 503 });
    };

    renderPanel(["calls:read", "calls:create"]);
    const user = userEvent.setup({ document });

    await user.click(await screen.findByRole("radio", { name: "4 out of 5" }));
    await user.click(screen.getByRole("button", { name: /submit review/i }));

    await waitFor(() => assert.equal(submitted, 1));
    const retry = await screen.findByRole("button", { name: /try again/i });
    await user.click(retry);
    await waitFor(() => assert.equal(submitted, 2));
});

// ── no promise the platform cannot keep ─────────────────────────────────────
//
// The reward ledger is real code, but it is switched off everywhere it runs:
// `REVIEW_REWARDS_ENABLED` (conversation_review_service.py:94, default "false")
// appears in no .env.example, no deploy script and no systemd unit, so the award
// path returns 0 and no ledger row is ever written. The UI used to render
// "Earns 10 points" / "points added" from the API's `rewards_enabled` flag,
// which meant one env var away from promising a user something nobody can pay.
//
// These two tests assert the ABSENCE of the vocabulary, with the API reporting
// rewards as ON and an award as granted — the most favourable input the old copy
// had. If someone re-adds the wording, this goes red rather than shipping.

const REWARD_WORDS = [
    /\bpoints?\b/i,
    /\brewards?\b/i,
    /\bcredits?\b/i,
    // \b keeps "Learn more" out of it: the 'e' in "learn" is not a word start.
    /\bearn(s|ed|ing)?\b/i,
];

function assertNoRewardPromise(text: string) {
    for (const word of REWARD_WORDS) {
        assert.equal(
            word.test(text),
            false,
            `review surface must not promise ${word} — the reward path is inert in production`,
        );
    }
}

test("the review form promises no points, rewards or credits — even with rewards reported ON", async () => {
    stubReads();
    extendedApi.getReviewOptions = async () => ({
        tags: ["good_conversation", "response_too_long"],
        rewards_enabled: true,
        points_per_review: 10,
        daily_cap: 20,
        bare_rating_earns_reward: false,
    });
    extendedApi.getMyReview = async () => null;
    extendedApi.listCallReviews = async () => [];

    const { container } = renderPanel(["calls:read", "calls:create"]);
    const user = userEvent.setup({ document });

    await screen.findByRole("button", { name: /submit review/i });
    assertNoRewardPromise(container.textContent ?? "");

    // A rating used to be the trigger for the eligibility line, so check again
    // once one is picked and again once detail is added.
    await user.click(screen.getByRole("radio", { name: "4 out of 5" }));
    assertNoRewardPromise(container.textContent ?? "");

    await user.click(screen.getByRole("button", { name: /good conversation/i }));
    assertNoRewardPromise(container.textContent ?? "");
});

test("a saved review confirms the save without announcing an award", async () => {
    stubReads();
    extendedApi.getReviewOptions = async () => ({
        tags: ["good_conversation"],
        rewards_enabled: true,
        points_per_review: 10,
        daily_cap: 20,
        bare_rating_earns_reward: true,
    });
    extendedApi.submitReview = async () => ({
        id: "r-mine",
        call_id: "call-1",
        campaign_id: null,
        user_id: "u-1",
        rating: 4,
        tags: [],
        comment: null,
        prompt_template: null,
        prompt_version: null,
        prompt_hash: null,
        // The server says it granted some. The UI still must not say so.
        awarded_points: 10,
        created_at: "2026-08-01T10:00:00Z",
        updated_at: "2026-08-01T10:00:00Z",
    });

    const { container } = renderPanel(["calls:read", "calls:create"]);
    const user = userEvent.setup({ document });

    await user.click(await screen.findByRole("radio", { name: "4 out of 5" }));
    await user.click(screen.getByRole("button", { name: /submit review/i }));

    await screen.findByText(/review saved/i);
    assertNoRewardPromise(container.textContent ?? "");
});

test("an existing review can be edited without any wording about awards", async () => {
    stubReads();
    extendedApi.getReviewOptions = async () => ({
        tags: ["good_conversation"],
        rewards_enabled: true,
        points_per_review: 10,
        daily_cap: 20,
        bare_rating_earns_reward: true,
    });
    extendedApi.getMyReview = async () => ({
        id: "r-mine",
        call_id: "call-1",
        campaign_id: null,
        user_id: "u-1",
        rating: 3,
        tags: [],
        comment: null,
        prompt_template: null,
        prompt_version: null,
        prompt_hash: null,
        awarded_points: 10,
        created_at: "2026-08-01T10:00:00Z",
        updated_at: "2026-08-01T10:00:00Z",
    });

    const { container } = renderPanel(["calls:read", "calls:create"]);

    await screen.findByRole("button", { name: /update review/i });
    assertNoRewardPromise(container.textContent ?? "");
});
