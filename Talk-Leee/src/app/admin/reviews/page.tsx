"use client";

/**
 * Agent reviews — the management view, inside the admin panel (goals.md §3).
 *
 * Lives at /admin/reviews, not a top-level route. Reviews are SUBMITTED from
 * wherever the recording is played — a thumb next to the play button in the
 * calls list and on the recordings page — and they surface HERE once left.
 * Those are two different jobs for two different people: everyone rates, admins
 * read the aggregate. The backend already agreed (`require_admin_tenant`); only
 * the navigation disagreed, and a non-admin clicking it got a bare 403.
 * `/reviews` redirects here so existing links survive.
 *
 * §3's acceptance criteria: "Admin can filter results by campaign, prompt
 * version, rating and tag." All four are here.
 *
 * The two aggregation tables are the point, not decoration. §3's Safe
 * Improvement Loop says: aggregate reviews by prompt version and failure
 * category, manually verify low-rated calls against recordings, convert
 * verified problems into evaluation cases. This page answers the first step —
 * *which prompt version is doing worse, and at what* — and links straight to the
 * calls for the second, because a low rating is a claim to check, not a fact.
 *
 * "Needs listening" counts 1s and 2s specifically. That is the queue.
 */
import { useMemo, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle, ChevronRight, Loader2, MessageSquare, Star } from "lucide-react";

import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { extendedApi } from "@/lib/extended-api";

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

export default function ReviewsPage() {
    const [promptVersion, setPromptVersion] = useState("");
    const [tag, setTag] = useState("");
    const [ratingMax, setRatingMax] = useState<number | "">("");
    const [page, setPage] = useState(1);

    const filters = useMemo(
        () => ({
            prompt_version: promptVersion || undefined,
            tag: tag || undefined,
            rating_max: ratingMax === "" ? undefined : Number(ratingMax),
            page,
            page_size: 25,
        }),
        [promptVersion, tag, ratingMax, page],
    );

    const summary = useQuery({
        queryKey: ["reviewSummary", promptVersion],
        queryFn: () => extendedApi.getReviewSummary({ prompt_version: promptVersion || undefined }),
    });
    const list = useQuery({
        queryKey: ["reviewList", filters],
        queryFn: () => extendedApi.listReviews(filters),
    });
    const options = useQuery({
        queryKey: ["reviewOptions"],
        queryFn: () => extendedApi.getReviewOptions(),
        staleTime: 5 * 60_000,
    });

    // 403 here means the account can see calls but not the tenant-wide view.
    const forbidden = (list.error as { status?: number })?.status === 403;

    const reset = () => { setPromptVersion(""); setTag(""); setRatingMax(""); setPage(1); };
    const t = summary.data?.totals;

    return (
        <DashboardLayout
            title="Agent reviews"
            description="What reviewers said about the agent, grouped by prompt version and failure category."
        >
            {forbidden ? (
                <div className="content-card">
                    <p className="flex items-center gap-2 text-sm text-muted-foreground">
                        <AlertCircle className="h-4 w-4" />
                        You need an admin role to see reviews across the workspace.
                    </p>
                </div>
            ) : (
                <div className="space-y-6">
                    {/* headline numbers */}
                    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                        <Stat label="Reviews" value={t?.reviews ?? 0} loading={summary.isLoading} />
                        <Stat
                            label="Average rating"
                            value={t?.avg_rating != null ? `${t.avg_rating} / 5` : "--"}
                            loading={summary.isLoading}
                        />
                        <Stat
                            label="Needs listening"
                            value={t?.low_rated ?? 0}
                            hint="rated 1 or 2"
                            loading={summary.isLoading}
                            emphasise={(t?.low_rated ?? 0) > 0}
                        />
                        <Stat label="Calls reviewed" value={t?.calls_reviewed ?? 0} loading={summary.isLoading} />
                    </div>

                    {/* the Safe Improvement Loop's two questions */}
                    <div className="grid gap-4 lg:grid-cols-2">
                        <Card title="By prompt version" hint="Which revision is doing worse">
                            {(summary.data?.by_prompt_version ?? []).length === 0 ? (
                                <Empty>No reviews yet.</Empty>
                            ) : (
                                <table className="w-full text-sm">
                                    <thead>
                                        <tr className="border-b border-border text-left text-xs text-muted-foreground">
                                            <th className="pb-2 font-medium">Version</th>
                                            <th className="pb-2 text-right font-medium">Reviews</th>
                                            <th className="pb-2 text-right font-medium">Avg</th>
                                            <th className="pb-2 text-right font-medium">Low</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {summary.data!.by_prompt_version.map((r) => (
                                            <tr key={r.prompt_version} className="border-b border-border/50 last:border-0">
                                                <td className="py-2">
                                                    <button
                                                        type="button"
                                                        onClick={() => {
                                                            setPromptVersion(
                                                                r.prompt_version === "unrecorded" ? "" : r.prompt_version,
                                                            );
                                                            setPage(1);
                                                        }}
                                                        className="font-mono text-xs underline-offset-2 hover:underline"
                                                    >
                                                        {r.prompt_version}
                                                    </button>
                                                </td>
                                                <td className="py-2 text-right tabular-nums">{r.reviews}</td>
                                                <td className="py-2 text-right tabular-nums">{r.avg_rating ?? "--"}</td>
                                                <td className={`py-2 text-right tabular-nums ${r.low_rated > 0 ? "text-amber-600" : ""}`}>
                                                    {r.low_rated}
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            )}
                        </Card>

                        <Card title="By failure category" hint="What actually goes wrong">
                            {(summary.data?.by_tag ?? []).length === 0 ? (
                                <Empty>No tagged reviews yet.</Empty>
                            ) : (
                                <ul className="space-y-1.5">
                                    {summary.data!.by_tag.map((r) => (
                                        <li key={r.tag}>
                                            <button
                                                type="button"
                                                onClick={() => { setTag(r.tag); setPage(1); }}
                                                className="flex w-full items-center gap-3 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-accent"
                                            >
                                                <span className="flex-1">{TAG_LABELS[r.tag] ?? r.tag}</span>
                                                <span className="tabular-nums text-muted-foreground">{r.reviews}</span>
                                                <span className="w-14 text-right text-xs tabular-nums text-muted-foreground">
                                                    {r.avg_rating ?? "--"} avg
                                                </span>
                                            </button>
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </Card>
                    </div>

                    {/* filters — the four axes §3 names */}
                    <div className="content-card">
                        <div className="flex flex-wrap items-end gap-3">
                            <Field label="Prompt version">
                                <input
                                    value={promptVersion}
                                    onChange={(e) => { setPromptVersion(e.target.value); setPage(1); }}
                                    placeholder="e.g. lead_gen@2"
                                    className="h-9 w-44 rounded-lg border border-border bg-background px-3 text-sm"
                                />
                            </Field>
                            <Field label="Failure category">
                                <select
                                    value={tag}
                                    onChange={(e) => { setTag(e.target.value); setPage(1); }}
                                    className="h-9 w-52 rounded-lg border border-border bg-background px-2 text-sm"
                                >
                                    <option value="">Any</option>
                                    {(options.data?.tags ?? []).map((x) => (
                                        <option key={x} value={x}>{TAG_LABELS[x] ?? x}</option>
                                    ))}
                                </select>
                            </Field>
                            <Field label="Rating at most">
                                <select
                                    value={ratingMax}
                                    onChange={(e) => {
                                        setRatingMax(e.target.value === "" ? "" : Number(e.target.value));
                                        setPage(1);
                                    }}
                                    className="h-9 w-28 rounded-lg border border-border bg-background px-2 text-sm"
                                >
                                    <option value="">Any</option>
                                    {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}
                                </select>
                            </Field>
                            <Button size="sm" variant="ghost" onClick={reset}>Clear</Button>
                            <span className="ml-auto text-xs text-muted-foreground">
                                {list.data ? `${list.data.total} matching` : ""}
                            </span>
                        </div>
                    </div>

                    {/* the reviews */}
                    <div className="content-card">
                        {list.isLoading ? (
                            <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
                                <Loader2 className="h-4 w-4 animate-spin" /> Loading reviews…
                            </div>
                        ) : (list.data?.items.length ?? 0) === 0 ? (
                            <Empty>
                                No reviews match. Reviews are left on a call&apos;s page, under the recording.
                            </Empty>
                        ) : (
                            <>
                                <ul className="divide-y divide-border">
                                    {list.data!.items.map((r) => (
                                        <li key={r.id} className="flex items-start gap-4 py-3">
                                            <span className="flex w-14 shrink-0 items-center gap-1 pt-0.5">
                                                <Star
                                                    className={`h-4 w-4 ${
                                                        r.rating <= 2
                                                            ? "fill-amber-500 text-amber-500"
                                                            : "fill-muted-foreground/40 text-muted-foreground/40"
                                                    }`}
                                                />
                                                <span className="text-sm font-medium tabular-nums">{r.rating}/5</span>
                                            </span>
                                            <div className="min-w-0 flex-1">
                                                {r.tags.length > 0 && (
                                                    <div className="mb-1 flex flex-wrap gap-1">
                                                        {r.tags.map((x) => (
                                                            <span
                                                                key={x}
                                                                className="rounded-full border border-border bg-muted/60 px-2 py-0.5 text-[11px] text-muted-foreground"
                                                            >
                                                                {TAG_LABELS[x] ?? x}
                                                            </span>
                                                        ))}
                                                    </div>
                                                )}
                                                {r.comment && (
                                                    <p className="text-sm text-foreground/90">{r.comment}</p>
                                                )}
                                                <p className="mt-1 text-xs text-muted-foreground">
                                                    {new Date(r.created_at).toLocaleString()}
                                                    {r.prompt_version && (
                                                        <span className="ml-2 font-mono">{r.prompt_version}</span>
                                                    )}
                                                </p>
                                            </div>
                                            {/* §3: verify low-rated calls against the recording before
                                                acting. One click to the evidence. */}
                                            <Link
                                                href={`/calls/${r.call_id}`}
                                                className="inline-flex h-8 shrink-0 items-center gap-1 rounded-lg border border-border px-2 text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
                                            >
                                                Listen <ChevronRight className="h-3 w-3" />
                                            </Link>
                                        </li>
                                    ))}
                                </ul>
                                {list.data!.total > list.data!.page_size && (
                                    <div className="mt-4 flex items-center justify-between text-sm">
                                        <Button
                                            size="sm"
                                            variant="outline"
                                            disabled={page <= 1}
                                            onClick={() => setPage((p) => Math.max(1, p - 1))}
                                        >
                                            Previous
                                        </Button>
                                        <span className="text-xs text-muted-foreground">
                                            Page {list.data!.page} of{" "}
                                            {Math.ceil(list.data!.total / list.data!.page_size)}
                                        </span>
                                        <Button
                                            size="sm"
                                            variant="outline"
                                            disabled={page * list.data!.page_size >= list.data!.total}
                                            onClick={() => setPage((p) => p + 1)}
                                        >
                                            Next
                                        </Button>
                                    </div>
                                )}
                            </>
                        )}
                    </div>
                </div>
            )}
        </DashboardLayout>
    );
}

function Stat({
    label, value, hint, loading, emphasise,
}: {
    label: string; value: React.ReactNode; hint?: string; loading?: boolean; emphasise?: boolean;
}) {
    return (
        <div className="content-card">
            <p className="text-xs text-muted-foreground">{label}</p>
            <p className={`mt-1 text-2xl font-semibold tabular-nums ${emphasise ? "text-amber-600" : ""}`}>
                {loading ? <Loader2 className="h-5 w-5 animate-spin" /> : value}
            </p>
            {hint && <p className="text-[11px] text-muted-foreground">{hint}</p>}
        </div>
    );
}

function Card({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
    return (
        <div className="content-card">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
                <MessageSquare className="h-4 w-4 text-muted-foreground" aria-hidden />
                {title}
            </h2>
            {hint && <p className="mb-3 text-xs text-muted-foreground">{hint}</p>}
            {children}
        </div>
    );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
    return (
        <label className="flex flex-col gap-1">
            <span className="text-xs text-muted-foreground">{label}</span>
            {children}
        </label>
    );
}

function Empty({ children }: { children: React.ReactNode }) {
    return <p className="py-6 text-center text-sm text-muted-foreground">{children}</p>;
}
