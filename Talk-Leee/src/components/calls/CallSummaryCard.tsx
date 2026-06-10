"use client";

import { Loader2, AlertCircle, TrendingUp, TrendingDown, Minus } from "lucide-react";
import type { CallSummaryObj, CallSummaryEnvelope } from "@/lib/dashboard-api";

// ---------------------------------------------------------------------------
// Outcome chip
// ---------------------------------------------------------------------------

function outcomeColor(outcome: string) {
    const o = outcome.toLowerCase();
    if (o.includes("positive") || o.includes("qualified") || o.includes("achieved") || o.includes("success"))
        return "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300";
    if (o.includes("negative") || o.includes("disqualified") || o.includes("not_achieved") || o.includes("fail"))
        return "border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-300";
    return "border-muted-foreground/30 bg-muted text-muted-foreground";
}

function SentimentIcon({ sentiment }: { sentiment: string }) {
    const s = sentiment.toLowerCase();
    if (s.includes("positive") || s.includes("good") || s.includes("warm"))
        return <TrendingUp className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />;
    if (s.includes("negative") || s.includes("bad") || s.includes("cold") || s.includes("hostile"))
        return <TrendingDown className="h-3.5 w-3.5 text-red-600 dark:text-red-400" />;
    return <Minus className="h-3.5 w-3.5 text-muted-foreground" />;
}

// ---------------------------------------------------------------------------
// Section helpers
// ---------------------------------------------------------------------------

function SectionHeading({ children }: { children: React.ReactNode }) {
    return (
        <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            {children}
        </h4>
    );
}

function BulletList({ items }: { items: string[] }) {
    return (
        <ul className="space-y-0.5">
            {items.map((item, i) => (
                <li key={i} className="flex gap-2 text-sm text-foreground leading-relaxed">
                    <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-muted-foreground/50" />
                    <span>{item}</span>
                </li>
            ))}
        </ul>
    );
}

// ---------------------------------------------------------------------------
// Main card
// ---------------------------------------------------------------------------

function SummaryBody({ summary }: { summary: CallSummaryObj }) {
    const hasKeyPoints = summary.key_points.length > 0;
    const hasObjections = summary.objections.length > 0;
    const hasCommitments = summary.commitments.length > 0;
    const hasActionItems = summary.action_items.length > 0;
    const hasNextStep = Boolean(summary.next_step?.trim());
    const hasFollowUpTips = (summary.follow_up_tips?.length ?? 0) > 0;
    const hasNotableQuotes = summary.notable_quotes.length > 0;

    return (
        <div className="space-y-4">
            {/* Header row: outcome chip + sentiment */}
            <div className="flex flex-wrap items-center gap-2">
                {summary.outcome && (
                    <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold ${outcomeColor(summary.outcome)}`}>
                        {summary.outcome.replace(/_/g, " ")}
                    </span>
                )}
                {summary.sentiment && (
                    <span className="inline-flex items-center gap-1 rounded-full border border-muted-foreground/20 bg-muted px-2.5 py-0.5 text-xs text-muted-foreground">
                        <SentimentIcon sentiment={summary.sentiment} />
                        {summary.sentiment}
                    </span>
                )}
            </div>

            {/* What happened */}
            {summary.what_happened?.trim() && (
                <p className="text-sm text-foreground leading-relaxed">{summary.what_happened}</p>
            )}

            {/* Key points */}
            {hasKeyPoints && (
                <div>
                    <SectionHeading>Key Points</SectionHeading>
                    <BulletList items={summary.key_points} />
                </div>
            )}

            {/* Objections */}
            {hasObjections && (
                <div>
                    <SectionHeading>Objections</SectionHeading>
                    <div className="space-y-2">
                        {summary.objections.map((obj, i) => (
                            <div key={i} className="rounded-lg border border-border bg-background px-3 py-2">
                                <p className="text-xs font-semibold text-muted-foreground mb-0.5">Objection</p>
                                <p className="text-sm text-foreground leading-relaxed">{obj.objection}</p>
                                {obj.handled?.trim() && (
                                    <>
                                        <p className="mt-1.5 text-xs font-semibold text-emerald-600 dark:text-emerald-400 mb-0.5">Handled</p>
                                        <p className="text-sm text-foreground leading-relaxed">{obj.handled}</p>
                                    </>
                                )}
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {/* Commitments */}
            {hasCommitments && (
                <div>
                    <SectionHeading>Commitments</SectionHeading>
                    <BulletList items={summary.commitments} />
                </div>
            )}

            {/* Action items */}
            {hasActionItems && (
                <div>
                    <SectionHeading>Action Items</SectionHeading>
                    <ul className="space-y-1">
                        {summary.action_items.map((ai, i) => (
                            <li key={i} className="flex items-start gap-2 text-sm text-foreground leading-relaxed">
                                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-muted-foreground/50" />
                                <span>
                                    {ai.item}
                                    {ai.owner?.trim() && (
                                        <span className="ml-1.5 text-xs text-muted-foreground">· {ai.owner}</span>
                                    )}
                                </span>
                            </li>
                        ))}
                    </ul>
                </div>
            )}

            {/* Next step */}
            {hasNextStep && (
                <div>
                    <SectionHeading>Next Step</SectionHeading>
                    <p className="text-sm text-foreground leading-relaxed">{summary.next_step}</p>
                </div>
            )}

            {/* Follow-up tips — actionable guidance, visually emphasized */}
            {hasFollowUpTips && (
                <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 px-3 py-2.5">
                    <h4 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-emerald-700 dark:text-emerald-400">
                        Follow-up Tips
                    </h4>
                    <ul className="space-y-1">
                        {summary.follow_up_tips!.map((tip, i) => (
                            <li key={i} className="flex gap-2 text-sm text-foreground leading-relaxed">
                                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500/60" />
                                <span>{tip}</span>
                            </li>
                        ))}
                    </ul>
                </div>
            )}

            {/* Notable quotes */}
            {hasNotableQuotes && (
                <div>
                    <SectionHeading>Notable Quotes</SectionHeading>
                    <div className="space-y-1.5">
                        {summary.notable_quotes.map((q, i) => (
                            <blockquote
                                key={i}
                                className="border-l-2 border-muted-foreground/40 pl-3 text-sm italic text-muted-foreground leading-relaxed"
                            >
                                &ldquo;{q}&rdquo;
                            </blockquote>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
}

// ---------------------------------------------------------------------------
// Public component — accepts the query result states
// ---------------------------------------------------------------------------

type CallSummaryCardProps = {
    isLoading: boolean;
    isError: boolean;
    error?: unknown;
    data?: CallSummaryEnvelope;
};

export function CallSummaryCard({ isLoading, isError, error, data }: CallSummaryCardProps) {
    if (isLoading) {
        return (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                Generating AI summary…
            </div>
        );
    }

    if (isError) {
        return (
            <div className="flex items-center gap-2 text-sm text-destructive">
                <AlertCircle className="h-4 w-4 shrink-0" />
                {error instanceof Error ? error.message : "Failed to load summary."}
            </div>
        );
    }

    if (!data) return null;

    if (!data.available || !data.summary) {
        return (
            <p className="text-sm text-muted-foreground">
                No summary — this call had no conversation to summarize.
            </p>
        );
    }

    return <SummaryBody summary={data.summary} />;
}
