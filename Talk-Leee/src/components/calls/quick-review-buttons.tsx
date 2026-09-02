"use client";

/**
 * Thumbs up / thumbs down, sitting directly beside a recording's play button.
 *
 * WHY THIS EXISTS SEPARATELY FROM THE FULL REVIEW PANEL
 * -----------------------------------------------------
 * goals.md §3 asks for "1-5 rating OR thumbs-up/thumbs-down". We built the
 * five-star panel first and put it on the call DETAIL page only, which meant
 * rating a call cost a page navigation — so in practice nobody rates anything
 * while working through a list of recordings. The judgement happens the moment
 * the audio stops, and the control has to be there at that moment.
 *
 * So this is the fast path: listen, click a thumb, move on. The panel on the
 * call page remains the considered path, for tags and a written comment.
 *
 * IT SHARES STATE WITH THE PANEL, DELIBERATELY
 * ---------------------------------------------
 * Same react-query key (`reviewQueryKey`), so a thumb clicked in the list is
 * already reflected when you open the call, and a rating set in the panel shows
 * as filled in the list. Two controls writing the same row must not disagree
 * about what that row says.
 *
 * IT MUST NOT DESTROY A WRITTEN REVIEW
 * -------------------------------------
 * `submitReview` is a PUT of the whole review. A naive quick-thumb would send
 * `tags: []` and `comment: null` and silently wipe a colleague's carefully
 * tagged assessment — the reviewer would have no idea. So the existing tags and
 * comment are read back and resent unchanged; a thumb edits the RATING only.
 */
import { useCallback, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, ThumbsDown, ThumbsUp } from "lucide-react";

import { extendedApi, type ConversationReview } from "@/lib/extended-api";
import { reviewQueryKey } from "./conversation-review-panel";

/**
 * Thumbs map onto the same 1-5 scale the panel writes, because the admin view
 * filters and aggregates on `rating` and a separate representation would split
 * the data in two.
 *
 * DOWN = 2, not 1. The management view's "needs listening" queue counts 1s and
 * 2s, so a thumbs-down lands in that queue as intended — while leaving 1
 * available to mean something worse, chosen deliberately in the full panel.
 */
const THUMBS_UP_RATING = 5;
const THUMBS_DOWN_RATING = 2;

export function QuickReviewButtons({
    callId,
    className = "",
    touchFriendly = false,
    onSaved,
}: {
    callId: string;
    className?: string;
    touchFriendly?: boolean;
    onSaved?: (review: ConversationReview) => void;
}) {
    const queryClient = useQueryClient();
    const [error, setError] = useState<string | null>(null);

    const mine = useQuery({
        queryKey: reviewQueryKey(callId),
        queryFn: () => extendedApi.getMyReview(callId),
        enabled: Boolean(callId),
        staleTime: 30_000,
    });

    const existing = mine.data ?? null;

    const save = useMutation({
        mutationFn: (rating: number) =>
            extendedApi.submitReview(callId, {
                rating,
                // Preserve, never clobber — see the file header.
                tags: existing?.tags ?? [],
                comment: existing?.comment ?? null,
            }),
        onSuccess: (saved: ConversationReview) => {
            queryClient.setQueryData(reviewQueryKey(callId), saved);
            void queryClient.invalidateQueries({ queryKey: ["conversationReviews", callId] });
            // The admin listing and its aggregates are now stale. Keys must
            // match /admin/reviews exactly or the management view keeps showing
            // yesterday's numbers after someone rates a call.
            void queryClient.invalidateQueries({ queryKey: ["reviewList"] });
            void queryClient.invalidateQueries({ queryKey: ["reviewSummary"] });
            setError(null);
            onSaved?.(saved);
        },
        onError: (err: unknown) =>
            setError(err instanceof Error ? err.message : "Couldn't save your rating"),
    });

    const rate = useCallback(
        (rating: number) => {
            setError(null);
            save.mutate(rating);
        },
        [save],
    );

    const current = existing?.rating ?? 0;
    const isUp = current >= 4;
    const isDown = current > 0 && current <= 2;
    const busy = save.isPending || mine.isLoading;

    const base = `inline-flex ${touchFriendly ? "h-11 w-11" : "h-8 w-8"} items-center justify-center rounded-lg border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50`;
    const idle =
        "border-border bg-background text-muted-foreground hover:bg-accent hover:text-foreground";

    return (
        <div className={`flex min-w-0 flex-col items-start gap-1 ${className}`}>
            <div className="flex items-center gap-1" role="group" aria-label="Rate how the agent handled this call">
                <button
                    type="button"
                    onClick={() => rate(THUMBS_UP_RATING)}
                    disabled={busy}
                    aria-pressed={isUp}
                    aria-label={isUp ? "Rated good — click to keep" : "Rate this conversation good"}
                    title={
                        error
                            ? error
                            : isUp
                                ? "You rated this good"
                                : "Good conversation"
                    }
                    className={`${base} ${isUp
                        ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                        : idle
                        }`}
                >
                    {busy && save.variables === THUMBS_UP_RATING ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                        <ThumbsUp className="h-4 w-4" />
                    )}
                </button>

                <button
                    type="button"
                    onClick={() => rate(THUMBS_DOWN_RATING)}
                    disabled={busy}
                    aria-pressed={isDown}
                    aria-label={
                        isDown ? "Rated poor — click to keep" : "Rate this conversation poor"
                    }
                    title={
                        error
                            ? error
                            : isDown
                                ? "You rated this poor — it is in the needs-listening queue"
                                : "Something went wrong in this call"
                    }
                    className={`${base} ${isDown
                        ? "border-red-500/50 bg-red-500/10 text-red-600 dark:text-red-400"
                        : idle
                        }`}
                >
                    {busy && save.variables === THUMBS_DOWN_RATING ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                        <ThumbsDown className="h-4 w-4" />
                    )}
                </button>
            </div>
            {error ? <p role="alert" className="max-w-44 text-[11px] leading-tight text-destructive">{error}</p> : null}
        </div>
    );
}
