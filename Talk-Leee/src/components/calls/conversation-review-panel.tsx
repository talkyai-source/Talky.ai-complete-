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
 * NO REWARD COPY — AND WHY IT WAS REMOVED
 * ---------------------------------------
 * §3 asks for reward eligibility to be shown before submission, and this panel
 * used to render it from the API's `rewards_enabled` / `points_per_review`
 * ("Earns 10 points", "points added"). That copy is gone.
 *
 * The reward path is inert in every environment that actually runs. It is gated
 * on `REVIEW_REWARDS_ENABLED` (backend conversation_review_service.py:94,
 * default "false"), and that variable appears in no .env.example, no deploy
 * script and no systemd unit — so the award is always 0 and no ledger row can
 * exist. Rendering eligibility from a flag that is structurally false either
 * says nothing (the common case) or, the moment anyone flips it in one place
 * without the ledger being reachable, promises a user something the platform
 * cannot deliver.
 *
 * The ledger plumbing is deliberately left alone — `awarded_points` still comes
 * back on the review and the backend still writes it when enabled. The rule
 * here is narrower: do not ADVERTISE a reward until it is actually paid. When
 * the env var is deployed and a ledger row can be shown to exist, put the copy
 * back and delete the guard test in this component's spec.
 */
import { useCallback, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Check, Loader2, MessageSquare, RotateCcw, Star } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
    extendedApi,
    type ConversationReview,
    type ReviewOptions,
} from "@/lib/extended-api";
import { useEffectivePermissions } from "@/lib/queries/inbound-queries";
import { getReviewCapabilities, isRetryableSubmitStatus } from "@/lib/review-permissions";

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

    // The form is gated on the permission the SUBMIT needs, not the one the
    // read needed. See getReviewCapabilities: the GET is calls:read and the PUT
    // is calls:create, so a readonly account loads its (empty) review perfectly
    // well and is refused only at the moment it presses Submit.
    const permissions = useEffectivePermissions();
    const permissionsSettled = permissions.isSuccess || permissions.isError;
    const reviewCapabilities = getReviewCapabilities(
        permissions.isSuccess ? permissions.data.permissions : undefined,
    );

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
    // The status is kept alongside the message because it decides whether a
    // retry is offered at all, and a formatted message cannot be interrogated
    // for that later.
    const [error, setError] = useState<{ message: string; status?: number } | null>(null);

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
        // eslint-disable-next-line react-hooks/set-state-in-effect -- seeds the edit form from a fetched/refetched review, keyed on id+updated_at so it doesn't clobber in-progress typing
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
            setError({
                message: err instanceof Error ? err.message : "Couldn't save your review",
                status: (err as { status?: number } | null)?.status,
            }),
    });

    const toggleTag = useCallback((tag: string) => {
        setJustSaved(null);
        setTags((prev) => (prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]));
    }, []);

    const opts = optionsQuery.data;

    const others = (othersQuery.data ?? []).filter((r) => r.id !== existing?.id);

    // Reading teammates' reviews is what calls:read buys, so it survives every
    // branch below — losing sight of them is not part of being unable to write.
    const othersSection = others.length > 0 ? (
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
    ) : null;

    if (mineQuery.isLoading || optionsQuery.isLoading || !permissionsSettled) {
        return (
            <Panel>
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Loader2 className="h-4 w-4 animate-spin" /> Loading review…
                </div>
            </Panel>
        );
    }

    // THE GATE IS ON WRITING, NOT ON READING.
    //
    // This used to key on a 403 from the read, which is the wrong signal twice
    // over: the read is gated on calls:read, so anyone who can open the call
    // page passes it, and the branch therefore never fired for the account it
    // was meant to protect. Meanwhile the submit is gated on calls:create, which
    // a readonly account does not hold — so it saw the whole form, typed a
    // review, and only learned it was not allowed after pressing Submit.
    //
    // A failed permission lookup is reported as a failed lookup rather than as a
    // refusal: "we could not check" and "you may not" are different facts, and
    // only one of them is about the person reading it.
    if (!reviewCapabilities.canWrite) {
        return (
            <Panel>
                <p className="flex items-center gap-2 text-sm text-muted-foreground">
                    <AlertCircle className="h-4 w-4 shrink-0" />
                    {reviewCapabilities.source === "unavailable"
                        ? "Your permissions couldn't be checked, so reviewing is unavailable right now."
                        : "You don't have permission to review calls. You can read reviews left by your teammates."}
                </p>
                {othersSection}
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

                {/* No reward eligibility line here on purpose — see the note at
                    the top of this file. What replaced it is the honest ask:
                    detail is worth adding because it is read, not because it
                    pays. */}
                {rating > 0 && !existing && (
                    <span className="text-xs text-muted-foreground">
                        A tag or a comment makes this far more useful to your team.
                    </span>
                )}
            </div>

            {justSaved && (
                <p className="mt-3 flex items-center gap-2 text-xs text-emerald-600">
                    <Check className="h-3.5 w-3.5" />
                    Review saved
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
                        <p>{error.message}</p>
                        {/* RETRY, not just a message (goals.md §3).
                            A save that fails on a flaky connection otherwise
                            leaves someone staring at their own typed review
                            with no way forward but to re-type it elsewhere —
                            the form still holds every word, so the only thing
                            missing was a button to send them again.

                            But only where a second attempt could actually
                            differ. On a 403 or a 422 the button re-sent the same
                            rejected request indefinitely, which looks like a way
                            out and is not one; that case gets a sentence
                            explaining why there is nothing to retry. */}
                        {isRetryableSubmitStatus(error.status) ? (
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
                        ) : (
                            <p className="text-muted-foreground">
                                {error.status === 403
                                    ? "Your account is not allowed to review calls, so retrying will not help."
                                    : "Sending this again would be refused the same way. Change the review, or ask an administrator."}
                            </p>
                        )}
                    </div>
                </div>
            )}

            {othersSection}
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
