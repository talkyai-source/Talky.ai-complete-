"use client";

import { Loader2, AlertCircle, TrendingUp, TrendingDown, Minus, Sparkles } from "lucide-react";
import type { CallSummaryObj, CallSummaryEnvelope } from "@/lib/dashboard-api";
import { InfoTip } from "@/components/ui/info-tip";

// ---------------------------------------------------------------------------
// Outcome chip
// ---------------------------------------------------------------------------

function outcomeColor(outcome: string) {
    const o = outcome.toLowerCase();
    // Check negative qualification labels first: "disqualified" and
    // "unqualified" both contain the substring "qualified".
    if (o.includes("negative") || o.includes("disqualified") || o.includes("unqualified") || o.includes("not_achieved") || o.includes("no_interest") || o.includes("fail"))
        return "border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-300";
    if (o.includes("positive") || o.includes("qualified") || o.includes("achieved") || o.includes("success"))
        return "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300";
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

function knownSummaryValue(value?: string): value is string {
    const normalized = value?.trim().toLowerCase();
    return Boolean(normalized && normalized !== "unknown" && normalized !== "none");
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
    const qualificationStatus = knownSummaryValue(summary.qualification_status)
        ? summary.qualification_status
        : null;
    const qualificationDetails = [
        { label: "Identified need", value: summary.identified_need },
        { label: "Decision role", value: summary.decision_maker_status },
        { label: "Timeline", value: summary.timeline },
        { label: "Budget", value: summary.budget_information },
    ].filter(
        (item): item is { label: string; value: string } => knownSummaryValue(item.value),
    );
    const hasQualification = Boolean(qualificationStatus || qualificationDetails.length > 0);

    return (
        <div className="space-y-4">
            {/* PROVENANCE, STATED ONCE AND IN THE PAGE (goals.md §8)
                §8 asks the summary to distinguish transcript facts from AI
                conclusions, and NOT to hide anything essential inside a
                tooltip. "This was written by a model and can be wrong" is
                essential, so it is a visible line — the tooltips below only
                explain the individual terms. */}
            <p className="flex items-start gap-1.5 rounded-lg border border-amber-500/25 bg-amber-500/5 px-2.5 py-2 text-xs text-muted-foreground">
                <Sparkles className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden />
                <span>
                    Written by a model from this call&apos;s transcript. Quotes are
                    taken from what was said; everything else — outcome, sentiment,
                    qualification — is the model&apos;s <strong>interpretation</strong> and
                    should be checked against the recording before it is acted on.
                </span>
            </p>

            {/* Header row: outcome chip + sentiment */}
            <div className="flex flex-wrap items-center gap-2">
                {summary.outcome && (
                    <span className="inline-flex items-center gap-1">
                        <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold ${outcomeColor(summary.outcome)}`}>
                            {summary.outcome.replace(/_/g, " ")}
                        </span>
                        <InfoTip label="About the call outcome">
                            <strong>Outcome</strong> — the model&apos;s verdict on how the call ended.
                            <br />
                            <strong>Qualified</strong>: the person matches what the campaign is
                            looking for. <strong>Interested</strong>: willing to hear more, not yet
                            qualified. <strong>Callback</strong>: asked to be reached at another
                            time. <strong>Unsuccessful</strong>: no answer, wrong person, or a clear
                            no.
                            <br />
                            An inference, not a fact from the transcript — confirm before treating
                            it as a lead.
                        </InfoTip>
                    </span>
                )}
                {summary.sentiment && (
                    <span className="inline-flex items-center gap-1">
                        <span className="inline-flex items-center gap-1 rounded-full border border-muted-foreground/20 bg-muted px-2.5 py-0.5 text-xs text-muted-foreground">
                            <SentimentIcon sentiment={summary.sentiment} />
                            {summary.sentiment}
                        </span>
                        <InfoTip label="About sentiment">
                            How the caller sounded overall, judged from their words rather than
                            their tone of voice — so sarcasm and irritation are easy to miss.
                            Treat it as a hint about which calls to listen to, not a measurement.
                        </InfoTip>
                    </span>
                )}
            </div>

            {/* What happened */}
            {summary.what_happened?.trim() && (
                <p className="text-sm text-foreground leading-relaxed">{summary.what_happened}</p>
            )}

            {/* Structured qualification — absent on historical summaries. */}
            {hasQualification && (
                <div className="rounded-lg border border-border bg-muted/20 px-3 py-2.5">
                    <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                        <span className="inline-flex items-center gap-1.5">
                            <SectionHeading>Qualification</SectionHeading>
                            <InfoTip label="About qualification">
                                Need, decision role, timeline and budget, as the model understood
                                them from the conversation.
                                <br />
                                A blank field means it was <strong>not established on the call</strong> —
                                not that the answer is no. Anything here is worth confirming
                                against the recording before it reaches a CRM.
                            </InfoTip>
                        </span>
                        {qualificationStatus && (
                            <span
                                className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold ${outcomeColor(qualificationStatus)}`}
                            >
                                {qualificationStatus.replace(/_/g, " ")}
                            </span>
                        )}
                    </div>
                    {qualificationDetails.length > 0 && (
                        <dl className="grid grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-2">
                            {qualificationDetails.map((item) => (
                                <div key={item.label}>
                                    <dt className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                                        {item.label}
                                    </dt>
                                    <dd className="text-sm text-foreground leading-relaxed">
                                        {item.value}
                                    </dd>
                                </div>
                            ))}
                        </dl>
                    )}
                </div>
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
