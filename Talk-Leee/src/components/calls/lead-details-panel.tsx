"use client";

/**
 * What the agent learned on this call, and how much to trust each piece.
 *
 * goals.md §7's frontend. The panel exists because a captured value is not a
 * fact — it has a provenance, and acting on an inference as though the caller
 * said it is exactly the failure the whole capture design was built to avoid.
 *
 * SO THE SOURCE IS NEVER HIDDEN
 * ------------------------------
 * Every value carries a visible chip: said by the caller, inferred by the
 * agent, from the imported list, edited by a person. §7 is explicit — "do not
 * treat inferred values as confirmed facts" — and the only way to honour that
 * in a UI is to refuse to render them identically. An amber "inferred" chip
 * next to a budget is the difference between a salesperson quoting it and a
 * salesperson checking it.
 *
 * MISSING IS ITS OWN STATE
 * -------------------------
 * A required field with no value is shown as an explicit gap, not an empty
 * row. And there are two kinds of empty, which the backend keeps apart and so
 * does this: a value of null means "we asked and they declined", an absent
 * field means "never established". Different follow-up.
 */
import { useCallback, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
    AlertCircle,
    Check,
    ClipboardList,
    Loader2,
    Pencil,
    Sparkles,
    X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { InfoTip } from "@/components/ui/info-tip";
import {
    SOURCE_LABEL,
    SOURCE_TONE,
    leadDetailsApi,
    type CapturedDetail,
} from "@/lib/lead-details-api";
import { leadInterestState } from "@/lib/lead-outcome";

export function leadDetailsQueryKey(callId: string) {
    return ["leadDetails", callId] as const;
}

function humanise(key: string) {
    return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function DetailRow({
    detail,
    callId,
}: {
    detail: CapturedDetail;
    callId: string;
}) {
    const queryClient = useQueryClient();
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState(detail.value ?? "");
    const [error, setError] = useState<string | null>(null);

    const save = useMutation({
        mutationFn: () =>
            leadDetailsApi.correct(callId, detail.field_key, draft.trim() || null,
                detail.field_type),
        onSuccess: () => {
            void queryClient.invalidateQueries({ queryKey: leadDetailsQueryKey(callId) });
            setEditing(false);
            setError(null);
        },
        onError: (e: unknown) =>
            setError(e instanceof Error ? e.message : "Couldn't save that change"),
    });

    // A value of null is NOT the same as never being asked — the backend keeps
    // them apart and so does this line.
    const declined = detail.value === null;

    return (
        <div className="flex flex-col gap-1 border-b border-border py-2.5 last:border-0">
            <div className="flex items-start justify-between gap-3">
                <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                    {humanise(detail.field_key)}
                    {detail.is_required && (
                        <span className="ml-1 text-red-500" aria-label="required">*</span>
                    )}
                </span>
                {!editing && (
                    <button
                        type="button"
                        onClick={() => { setDraft(detail.value ?? ""); setEditing(true); }}
                        aria-label={`Edit ${humanise(detail.field_key)}`}
                        className="shrink-0 text-muted-foreground transition-colors hover:text-foreground"
                    >
                        <Pencil className="h-3.5 w-3.5" />
                    </button>
                )}
            </div>

            {editing ? (
                <div className="flex flex-col gap-2">
                    <input
                        value={draft}
                        onChange={(e) => setDraft(e.target.value)}
                        autoFocus
                        className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm outline-none focus:border-ring"
                    />
                    <div className="flex items-center gap-2">
                        <Button size="sm" disabled={save.isPending} onClick={() => save.mutate()}>
                            {save.isPending
                                ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                : <Check className="h-3.5 w-3.5" />}
                            Save
                        </Button>
                        <button
                            type="button"
                            onClick={() => { setEditing(false); setError(null); }}
                            className="text-xs text-muted-foreground hover:text-foreground"
                        >
                            Cancel
                        </button>
                    </div>
                    {error && (
                        <p role="alert" className="text-xs text-red-600 dark:text-red-400">
                            {error}
                        </p>
                    )}
                </div>
            ) : (
                <p className={`text-sm leading-relaxed ${declined ? "italic text-muted-foreground" : "text-foreground"}`}>
                    {declined ? "Asked — the caller didn't give one" : detail.value}
                </p>
            )}

            <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
                <span
                    className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium ${SOURCE_TONE[detail.source]}`}
                >
                    {SOURCE_LABEL[detail.source]}
                </span>
                {detail.confirmed ? (
                    <span className="inline-flex items-center gap-1 text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
                        <Check className="h-3 w-3" /> confirmed on the call
                    </span>
                ) : (
                    <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
                        <AlertCircle className="h-3 w-3" /> not read back
                    </span>
                )}
            </div>
        </div>
    );
}

export function LeadDetailsPanel({
    callId,
    campaignId,
    leadOutcome,
}: {
    callId: string;
    campaignId?: string;
    leadOutcome?: string | null;
}) {
    const query = useQuery({
        queryKey: leadDetailsQueryKey(callId),
        queryFn: () => leadDetailsApi.detailsForCall(callId, campaignId),
        enabled: Boolean(callId),
    });

    const details = query.data?.details ?? [];
    const missing = query.data?.missing_required ?? [];

    // The badge comes from the post-call verdict, never from "some fields were
    // captured". A caller can give their name and still explicitly decline.
    const interested = leadInterestState(leadOutcome) === "interested";

    const retry = useCallback(() => void query.refetch(), [query]);

    if (query.isLoading) {
        return (
            <Panel>
                <p className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Loader2 className="h-4 w-4 animate-spin" /> Loading captured details…
                </p>
            </Panel>
        );
    }

    if (query.isError) {
        return (
            <Panel>
                <div className="flex items-start gap-2 text-sm text-destructive">
                    <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                    <div className="space-y-2">
                        <p>Couldn&apos;t load what this call captured.</p>
                        <Button variant="outline" size="sm" onClick={retry}>Try again</Button>
                    </div>
                </div>
            </Panel>
        );
    }

    if (!details.length && !missing.length) {
        return (
            <Panel>
                <p className="text-sm text-muted-foreground">
                    Nothing structured was captured on this call.
                </p>
            </Panel>
        );
    }

    return (
        <Panel>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
                    <ClipboardList className="h-4 w-4 text-muted-foreground" aria-hidden />
                    Lead details
                    <InfoTip label="About captured lead details">
                        What the agent got out of this conversation. Every value shows
                        where it came from — an <strong>inferred</strong> value is the
                        model&apos;s reading of the call, not something the caller said,
                        and should be checked before it reaches a CRM.
                    </InfoTip>
                </h3>
                {interested && (
                    <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/40 bg-emerald-500/10 px-2.5 py-0.5 text-[11px] font-semibold text-emerald-700 dark:text-emerald-300">
                        <Sparkles className="h-3 w-3" /> Interested lead
                    </span>
                )}
            </div>

            {missing.length > 0 && (
                <div className="mb-3 flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2">
                    <X className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-400" />
                    <p className="text-xs text-muted-foreground">
                        <strong className="text-foreground">Still missing:</strong>{" "}
                        {missing.map(humanise).join(", ")} — required by this campaign and
                        never established on the call.
                    </p>
                </div>
            )}

            <div>
                {details.map((d) => (
                    <DetailRow key={d.field_key} detail={d} callId={callId} />
                ))}
            </div>
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
