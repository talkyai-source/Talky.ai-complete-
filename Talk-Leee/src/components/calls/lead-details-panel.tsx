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
 * row. Empty values have several meanings; the recorded validation/evidence
 * status, not NULL alone, determines the follow-up.
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

const CONTACT_STATUS_LABEL: Record<string, string> = {
    awaiting_confirmation: "Awaiting caller confirmation",
    needs_clarification: "Needs clarification",
    invalid: "Invalid contact — needs correction",
    cancelled: "Withdrawn or replaced — do not use",
};

function sourceReference(value: unknown): { caller_turn_order: number; revision_sha256: string } | null {
    if (!value || typeof value !== "object") return null;
    const source = value as Record<string, unknown>;
    return typeof source.caller_turn_order === "number" && Number.isInteger(source.caller_turn_order)
        && source.caller_turn_order >= 0 && typeof source.revision_sha256 === "string"
        && /^[a-f0-9]{64}$/i.test(source.revision_sha256)
        ? { caller_turn_order: source.caller_turn_order, revision_sha256: source.revision_sha256 }
        : null;
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
            void queryClient.invalidateQueries({ queryKey: ["contact-lead-details"] });
            setEditing(false);
            setError(null);
        },
        onError: (e: unknown) =>
            setError(e instanceof Error ? e.message : "Couldn't save that change"),
    });

    const empty = detail.value === null;
    const contact = detail.field_type === "email" || detail.field_type === "phone";
    const statusLabel = CONTACT_STATUS_LABEL[detail.validation_status ?? ""];
    const confirmed = !empty && detail.confirmed
        && (!contact || detail.validation_status === "confirmed");
    const emptyLabel = statusLabel
        || (detail.evidence?.status === "declined" ? "The caller declined to provide this"
            : "No usable value was saved");
    const valueSource = sourceReference(detail.evidence?.value_source);
    const confirmationSource = sourceReference(detail.evidence?.confirmation_source);
    const statusSource = sourceReference(detail.evidence?.status_source);

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
                <p className={`text-sm leading-relaxed ${empty ? "italic text-muted-foreground" : "text-foreground"}`}>
                    {empty ? emptyLabel : detail.value}
                </p>
            )}

            <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
                <span
                    className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium ${SOURCE_TONE[detail.source]}`}
                >
                    {SOURCE_LABEL[detail.source]}
                </span>
                {detail.source === "manual_edit" && confirmed ? (
                    <span className="text-[10px] text-blue-600">Verified by a person</span>
                ) : confirmed ? (
                    <span className="inline-flex items-center gap-1 text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
                        <Check className="h-3 w-3" /> confirmed on the call
                    </span>
                ) : (
                    <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
                        <AlertCircle className="h-3 w-3" /> not confirmed by the caller
                    </span>
                )}
            </div>
            {!empty && statusLabel && <p className="text-xs text-muted-foreground">{statusLabel}</p>}
            {(valueSource || confirmationSource || statusSource) && (
                <details className="text-xs text-muted-foreground">
                    <summary className="cursor-pointer">Source evidence</summary>
                    {valueSource && <p>Captured from caller turn {valueSource.caller_turn_order}
                        {" · Revision "}{valueSource.revision_sha256.slice(0, 12)}</p>}
                    {confirmationSource && (
                        <p>{confirmed ? "Confirmation" : "Previous confirmation"} from caller turn {confirmationSource.caller_turn_order}
                            {" · Revision "}{confirmationSource.revision_sha256.slice(0, 12)}</p>
                    )}
                    {statusSource && <p>Status changed by caller turn {statusSource.caller_turn_order}
                        {" · Revision "}{statusSource.revision_sha256.slice(0, 12)}</p>}
                    {detail.evidence?.confirmation_evidence && (
                        <p>{humanise(detail.evidence.confirmation_evidence)}</p>
                    )}
                </details>
            )}
            {detail.evidence?.status && (
                <p className="text-xs text-muted-foreground">
                    {humanise(detail.evidence.status)}
                    {detail.evidence.time_resolution === "needs_review" ? " · Confirm the date and timezone before scheduling" : ""}
                </p>
            )}
            {detail.evidence?.source_quote && (
                <blockquote className="border-l-2 border-border pl-2 text-xs text-muted-foreground">
                    Caller: “{detail.evidence.source_quote}”
                </blockquote>
            )}
        </div>
    );
}

export function ContactCapturedDetails({ leadId, latestNote }: { leadId: string; latestNote?: string | null }) {
    const [open, setOpen] = useState(false);
    return (
        <div className="mt-2 whitespace-normal">
            {latestNote && <p className="mb-1 max-w-sm text-xs text-muted-foreground">Latest call analysis: {latestNote}</p>}
            <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
                className="text-xs font-medium text-primary hover:underline">
                {open ? "Hide captured details" : "View captured details"}
            </button>
            {open && <div className="mt-2 min-w-64"><LeadDetailsPanel leadId={leadId} /></div>}
        </div>
    );
}

export function LeadDetailsPanel({
    callId = "",
    leadId,
    campaignId,
    leadOutcome,
}: {
    callId?: string;
    leadId?: string;
    campaignId?: string;
    leadOutcome?: string | null;
}) {
    const query = useQuery({
        queryKey: leadId ? ["contact-lead-details", leadId] : leadDetailsQueryKey(callId),
        queryFn: () => leadId ? leadDetailsApi.detailsForLead(leadId) : leadDetailsApi.detailsForCall(callId, campaignId),
        enabled: Boolean(callId || leadId),
        refetchInterval: (state) => state.state.data?.processing_status === "pending"
            || state.state.data?.crm_deliveries?.some((item) => ["pending", "processing"].includes(item.status)) ? 5_000 : false,
    });

    const details = query.data?.details ?? [];
    const missing = query.data?.missing_required ?? [];
    const processing = query.data?.processing_status;
    const deliveries = query.data?.crm_deliveries ?? [];
    const evidenceSubject = leadId ? "the latest call" : "this call";
    const transcriptMessage = processing === "no_calls" ? undefined : ({
        partial: `The final transcript save for ${evidenceSubject} is not confirmed. Saved text may be incomplete; review the call history before using it.`,
        failed: `The final transcript for ${evidenceSubject} could not be saved. Earlier saved text may be incomplete; review the call history.`,
        unknown: `Transcript completeness for ${evidenceSubject} has not been verified.`,
    } as Record<string, string>)[query.data?.transcript_save_state ?? ""];
    const processingMessage = processing === "failed"
        ? "Some call details could not be processed. Saved details remain available."
        : processing === "pending" ? "Call details are awaiting analysis."
        : processing === "not_processed" ? "This older call has not been analyzed for captured details. Open its summary in call history to process it."
        : processing === "no_transcript" ? "No usable transcript is available for this call." : null;

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

    if (!details.length && !missing.length && !deliveries.length) {
        return (
            <Panel>
                {transcriptMessage && <p role="status" className="mb-3 text-sm text-muted-foreground">{transcriptMessage}</p>}
                <p className="text-sm text-muted-foreground">
                    {processingMessage || (processing === "no_calls" ? "No calls recorded for this contact." : "No captured details are available from the saved evidence.")}
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

            {processingMessage && <p role="status" className="mb-3 text-sm text-muted-foreground">{processingMessage}</p>}
            {transcriptMessage && <p role="status" className="mb-3 text-sm text-muted-foreground">{transcriptMessage}</p>}
            {deliveries.length > 0 && (
                <div className="mb-3 space-y-1 text-xs text-muted-foreground" aria-label="CRM delivery status">
                    {deliveries.map((delivery) => (
                        <div key={delivery.provider}>
                            <p>{humanise(delivery.provider)}: {({ succeeded: "Synced", pending: "Waiting to sync", processing: "Syncing", failed: "Sync failed", unknown: "Outcome uncertain — review before retrying", skipped: "Not synced" } as Record<string, string>)[delivery.status] ?? humanise(delivery.status)}</p>
                            {delivery.call_id && <details className="mt-1">
                                <summary className="cursor-pointer">{humanise(delivery.provider)} delivery record</summary>
                                <div className="space-y-1 break-all pl-2">
                                    <p>Call reference: {delivery.call_id}</p>
                                    {delivery.destination_account_id && <p>Original account: {delivery.destination_account_id}</p>}
                                    {delivery.remote_contact_id && <p>Provider contact reference: {delivery.remote_contact_id}</p>}
                                    {delivery.remote_call_id && <p>Provider call reference: {delivery.remote_call_id}</p>}
                                    {delivery.last_error && <p>{delivery.last_error}</p>}
                                    {delivery.status === "unknown" && <p>Ask your workspace administrator to review this record in the original account before sending again.</p>}
                                </div>
                            </details>}
                        </div>
                    ))}
                </div>
            )}
            {missing.length > 0 && (
                <div className="mb-3 flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2">
                    <X className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-400" />
                    <p className="text-xs text-muted-foreground">
                        <strong className="text-foreground">Still missing:</strong>{" "}
                        {missing.map(humanise).join(", ")} — required by this campaign and
                        not yet saved as usable information.
                    </p>
                </div>
            )}

            <div>
                {details.map((d) => (
                    <DetailRow key={d.field_key} detail={d} callId={d.call_id || callId} />
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
