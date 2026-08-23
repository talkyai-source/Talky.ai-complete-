"use client";

/**
 * "Review this conversation" — rating, structured tags, comment (goals.md §3).
 *
 * Sits beside the recording and transcript on the call page, because §3 asks
 * for exactly that: you listen, then you say what went wrong, and the tag you
 * pick is only meaningful if you just heard the thing you are describing.
 *
 * ONE REVIEW PER PERSON, NOT PER CALL
 * -----------------------------------
 * Different from the voice note on the same page. Submitting again edits your
 * own review; a teammate's review is separate and shown alongside. That is why
 * the button says "Update review" once you have left one — silently replacing
 * someone else's assessment would be worse than not having the feature.
 *
 * REWARD HONESTY
 * --------------
 * §3 wants reward eligibility shown BEFORE submission. The rules come from the
 * API (`/calls/reviews/options`) rather than being hardcoded, so the UI cannot
 * promise points that the server will decline to grant — and when rewards are
 * switched off entirely, it says nothing about them at all rather than dangling
 * something that will not arrive.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Check, Loader2, MessageSquare, RotateCcw, Star } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
    extendedApi,
    type ConversationReview,
    type ReviewOptions,
} from "@/lib/extended-api";

/** Human wording for the eleven machine tags. Order matches goals.md §3. */
const TAG_LABELS: Record<string, string> = {
    agent_did_not_understand: "Didn't understand",
    agent_interrupted_caller: "Interrupted the caller",
    agent_did_not_answer_question: "Didn't answer the question",
    response_too_long: "Response too long",
    response_too_slow: "Response too slow",
    agent_repeated_itself: "Repeated itself",
    wrong_qualification_question: "Wrong qualifying question",
    wrong_call_outcome: "Wrong call outcome",
    poor_objection_handling: "Poor objection handling",
    incorrect_information: "Incorrect information",
    good_conversation: "Good conversation",
};

const POSITIVE_TAGS = new Set(["good_conversation"]);

export function reviewQueryKey(callId: string) {
    return ["conversationReview", callId] as const;
}

export function ConversationReviewPanel({ callId }: { callId: string }) {
    const queryClient = useQueryClient();

    const optionsQuery = useQuery<ReviewOptions>({
        queryKey: ["reviewOptions"],
        queryFn: () => extendedApi.getReviewOptions(),
        staleTime: 5 * 60_000,
    });
    const mineQuery = useQuery({
        queryKey: reviewQueryKey(callId),
        queryFn: () => extendedApi.getMyReview(callId),
        enabled: Boolean(callId),
    });
    const othersQuery = useQuery({
        queryKey: ["conversationReviews", callId],
        queryFn: () => extendedApi.listCallReviews(callId),
        enabled: Boolean(callId),
    });

    const existing = mineQuery.data ?? null;
    const [rating, setRating] = useState(0);
    const [tags, setTags] = useState<string[]>([]);
    const [comment, setComment] = useState("");
    const [justSaved, setJustSaved] = useState<ConversationReview | null>(null);
    const [error, setError] = useState<string | null>(null);

    // Seed the form from an existing review so "edit" starts from what you
    // wrote, not from blank.
    //
    // The dependency list is deliberately id + updated_at rather than the
    // `existing` object, and the lint rule is suppressed rather than obeyed:
    // react-query hands back a NEW object identity on every refetch, so
    // depending on `existing` would re-run this effect while someone is typing
    // and silently discard their edit. Keying on the two fields that actually
    // change means it re-seeds when the saved review really changed, and stays
    // out of the way otherwise.
    useEffect(() => {
        if (!existing) return;
        setRating(existing.rating);
        setTags(existing.tags ?? []);
        setComment(existing.comment ?? "");
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [existing?.id, existing?.updated_at]);

    const save = useMutation({
        mutationFn: () =>
            extendedApi.submitReview(callId, { rating, tags, comment: comment.trim() || null }),
        onSuccess: (saved) => {
            queryClient.setQueryData(reviewQueryKey(callId), saved);
            void queryClient.invalidateQueries({ queryKey: ["conversationReviews", callId] });
            setJustSaved(saved);
            setError(null);
        },
        onError: (err: unknown) =>
            setError(err instanceof Error ? err.message : "Couldn't save your review"),
    });

    const toggleTag = useCallback((tag: string) => {
        setJustSaved(null);
        setTags((prev) => (prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]));
    }, []);

    const opts = optionsQuery.data;
    const hasDetail = tags.length > 0 || comment.trim().length > 0;
    const wouldEarn = useMemo(() => {
        if (!opts?.rewards_enabled) return 0;
        if (existing) return 0;                       // edits never re-credit
        if (!hasDetail && !opts.bare_rating_earns_reward) return 0;
        return opts.points_per_review;
    }, [opts, existing, hasDetail]);

    const others = (othersQuery.data ?? []).filter((r) => r.id !== existing?.id);

    if (mineQuery.isLoading || optionsQuery.isLoading) {
        return (
            <Panel>
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Loader2 className="h-4 w-4 animate-spin" /> Loading review…
                </div>
            </Panel>
        );
    }

    // A 403 here means the account may see the call but not review it. Say that
    // rather than showing a form whose submit will always fail.
    if (mineQuery.isError && (mineQuery.error as { status?: number })?.status === 403) {
        return (
            <Panel>
                <p className="flex items-center gap-2 text-sm text-muted-foreground">
                    <AlertCircle className="h-4 w-4" />
                    You don&apos;t have permission to review calls.
                </p>
            </Panel>
        );
    }

    return (
        <Panel>
            <div className="mb-4 flex items-start justify-between gap-3">
                <div>
                    <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
                        <MessageSquare className="h-4 w-4 text-muted-foreground" aria-hidden />
                        Review this conversation
                    </h3>
                    <p className="text-xs text-muted-foreground">
                        {existing
                            ? "You reviewed this call. Changing it updates your review."
                            : "How did the agent handle it? Your rating is private to your team."}
                    </p>
                </div>
                {existing && (
                    <span className="shrink-0 rounded-full border border-border bg-muted/60 px-2 py-0.5 text-[11px] text-muted-foreground">
                        Edited {new Date(existing.updated_at).toLocaleDateString()}
                    </span>
                )}
            </div>

            {/* rating */}
            <fieldset className="mb-4">
                <legend className="mb-2 text-xs font-medium text-muted-foreground">
                    Overall rating
                </legend>
                <div className="flex items-center gap-1" role="radiogroup" aria-label="Overall rating">
                    {[1, 2, 3, 4, 5].map((n) => (
                        <button
                            key={n}
                            type="button"
                            role="radio"
                            aria-checked={rating === n}
                            aria-label={`${n} out of 5`}
                            onClick={() => { setRating(n); setJustSaved(null); }}
                            className="rounded-md p-1 transition-transform hover:scale-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        >
                            <Star
                                className={`h-6 w-6 ${
                                    n <= rating
                                        ? "fill-amber-400 text-amber-400"
                                        : "text-muted-foreground/40"
                                }`}
                            />
                        </button>
                    ))}
                    {rating > 0 && (
                        <span className="ml-2 text-xs text-muted-foreground">{rating} / 5</span>
                    )}
                </div>
            </fieldset>

            {/* tags */}
            <fieldset className="mb-4">
                <legend className="mb-2 text-xs font-medium text-muted-foreground">
                    What happened? (optional, choose any)
                </legend>
                <div className="flex flex-wrap gap-2">
                    {(opts?.tags ?? []).map((tag) => {
                        const on = tags.includes(tag);
                        const good = POSITIVE_TAGS.has(tag);
                        return (
                            <button
                                key={tag}
                                type="button"
                                aria-pressed={on}
                                onClick={() => toggleTag(tag)}
                                className={`rounded-full border px-3 py-1 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                                    on
                                        ? good
                                            ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-600"
                                            : "border-primary/50 bg-primary/10 text-primary"
                                        : "border-border bg-background text-muted-foreground hover:bg-accent hover:text-foreground"
                                }`}
                            >
                                {TAG_LABELS[tag] ?? tag}
                            </button>
                        );
                    })}
                </div>
            </fieldset>

            {/* comment */}
            <div className="mb-4">
                <label htmlFor="review-comment" className="mb-2 block text-xs font-medium text-muted-foreground">
                    Anything else? (optional)
                </label>
                <textarea
                    id="review-comment"
                    value={comment}
                    maxLength={4000}
                    rows={3}
                    onChange={(e) => { setComment(e.target.value); setJustSaved(null); }}
                    placeholder="e.g. it answered the price question well but booked the wrong slot"
                    className="w-full rounded-xl border border-border bg-background p-3 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
            </div>

            <div className="flex flex-wrap items-center gap-3">
                <Button
                    size="sm"
                    disabled={rating === 0 || save.isPending}
                    onClick={() => save.mutate()}
                >
                    {save.isPending ? (
                        <><Loader2 className="h-3.5 w-3.5 animate-spin" /> Saving…</>
                    ) : existing ? "Update review" : "Submit review"}
                </Button>

                {rating === 0 && (
                    <span className="text-xs text-muted-foreground">Pick a rating to continue.</span>
                )}

                {/* §3: reward eligibility BEFORE submission, never a promise the
                    server will decline to keep. */}
                {rating > 0 && opts?.rewards_enabled && !existing && (
                    <span className="text-xs text-muted-foreground">
                        {wouldEarn > 0
                            ? `Earns ${wouldEarn} points`
                            : "Add a tag or a comment to earn points"}
                    </span>
                )}
                {rating > 0 && opts?.rewards_enabled && existing && (
                    <span className="text-xs text-muted-foreground">
                        Editing won&apos;t award points again
                    </span>
                )}
            </div>

            {justSaved && (
                <p className="mt-3 flex items-center gap-2 text-xs text-emerald-600">
                    <Check className="h-3.5 w-3.5" />
                    Review saved
                    {justSaved.awarded_points > 0 && ` — ${justSaved.awarded_points} points added`}
                    {justSaved.prompt_version && (
                        <span className="text-muted-foreground">
                            · recorded against {justSaved.prompt_version}
                        </span>
                    )}
                </p>
            )}

            {error && (
                <div className="mt-3 flex items-start gap-2 text-xs text-destructive">
                    <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    <div className="space-y-1.5">
                        <p>{error}</p>
                        {/* RETRY, not just a message (goals.md §3).
                            A save that fails on a flaky connection otherwise
                            leaves someone staring at their own typed review
                            with no way forward but to re-type it elsewhere —
                            the form still holds every word, so the only thing
                            missing was a button to send them again. */}
                        <button
                            type="button"
                            onClick={() => { setError(null); save.mutate(); }}
                            disabled={save.isPending}
                            className="inline-flex items-center gap-1.5 rounded-md border border-destructive/40 px-2 py-1 font-medium text-destructive transition-colors hover:bg-destructive/10 disabled:opacity-50"
                        >
                            {save.isPending
                                ? <Loader2 className="h-3 w-3 animate-spin" />
                                : <RotateCcw className="h-3 w-3" />}
                            Try again
                        </button>
                    </div>
                </div>
            )}

            {others.length > 0 && (
                <div className="mt-5 border-t border-border pt-4">
                    <h4 className="mb-2 text-xs font-medium text-muted-foreground">
                        {others.length} other review{others.length > 1 ? "s" : ""} on this call
                    </h4>
                    <ul className="space-y-2">
                        {others.map((r) => (
                            <li key={r.id} className="rounded-lg border border-border bg-muted/40 p-2">
                                <div className="flex items-center gap-2 text-xs">
                                    <span className="font-mono">{r.rating}/5</span>
                                    <span className="text-muted-foreground">
                                        {new Date(r.created_at).toLocaleDateString()}
                                    </span>
                                </div>
                                {r.tags.length > 0 && (
                                    <p className="mt-1 text-[11px] text-muted-foreground">
                                        {r.tags.map((t) => TAG_LABELS[t] ?? t).join(" · ")}
                                    </p>
                                )}
                                {r.comment && (
                                    <p className="mt-1 text-xs text-foreground/90">{r.comment}</p>
                                )}
                            </li>
                        ))}
                    </ul>
                </div>
            )}
        </Panel>
    );
}

function Panel({ children }: { children: React.ReactNode }) {
    return (
        <section className="rounded-2xl border border-border bg-background p-4 shadow-sm">
            {children}
        </section>
    );
}
