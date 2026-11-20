"use client";

import { useEffect, useId, useRef, useState } from "react";
import { Pencil } from "lucide-react";

import { Button } from "@/components/ui/button";
import { LeadTypeSelect, leadTypeAccent } from "@/components/calls/call-history-workflow-controls";
import type { Call } from "@/lib/dashboard-api";
import {
    defaultCallHistoryWorkflow,
    type CallHistoryLeadType,
    type CallHistoryWorkflowEntry,
} from "@/lib/call-history-workflow";
import { callerGaveProblem, correctCallerGave, type CallerGaveKind } from "@/lib/call-contact-correction";
import type { CallWorkflowPatch } from "@/lib/use-call-history-workflow";

export const READ_ONLY_SENTENCE = "Your role cannot edit call details.";
export const NOT_SAVED_SENTENCE = "Not saved. Try again.";

interface Draft {
    leadType: CallHistoryLeadType;
    notes: string;
    contact: string;
    interest: string;
    nextStep: string;
    completed: boolean;
    email: string;
    phone: string;
}

export interface CallEditPanelProps {
    call: Call;
    entry: CallHistoryWorkflowEntry | undefined;
    canEdit: boolean;
    busy: boolean;
    failed: boolean;
    onRetry: () => void;
    onWorkflowChange: (call: Call, patch: CallWorkflowPatch) => void;
    /** Called after a save so the page can refetch the call ("Caller gave" shows the stored values). */
    onSaved: () => void;
    /** Test seam; production callers never pass this. */
    correctFn?: typeof correctCallerGave;
}

function draftFrom(call: Call, entry: CallHistoryWorkflowEntry | undefined): Draft {
    const saved = entry ?? defaultCallHistoryWorkflow(call);
    return {
        leadType: saved.leadType,
        notes: saved.notes,
        contact: saved.form.contact,
        interest: saved.form.interest,
        nextStep: saved.form.nextStep,
        completed: saved.form.completed,
        email: call.captured_email ?? "",
        phone: call.captured_phone ?? "",
    };
}

const INPUT_CLASS = "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none placeholder:text-muted-foreground focus:border-ring/50 focus:ring-2 focus:ring-ring/30";
const LABEL_CLASS = "text-xs font-semibold uppercase tracking-wide text-muted-foreground";

function ReadRow({ label, value, empty }: { label: string; value: string; empty: string }) {
    return (
        <div>
            <dt className="text-[11px] font-bold uppercase tracking-wide text-muted-foreground">{label}</dt>
            <dd className={`mt-0.5 whitespace-pre-wrap break-words text-sm ${value ? "text-foreground" : "text-muted-foreground"}`}>
                {value || empty}
            </dd>
        </div>
    );
}

export function CallEditPanel({
    call,
    entry,
    canEdit,
    busy,
    failed,
    onRetry,
    onWorkflowChange,
    onSaved,
    correctFn = correctCallerGave,
}: CallEditPanelProps) {
    const [editing, setEditing] = useState(false);
    const [saving, setSaving] = useState(false);
    const [draft, setDraft] = useState<Draft>(() => draftFrom(call, entry));
    const [problems, setProblems] = useState<{ email?: string; phone?: string }>({});
    const [saveError, setSaveError] = useState<string[] | null>(null);
    const formRef = useRef<HTMLFormElement>(null);
    const notesId = useId();
    const contactId = useId();
    const interestId = useId();
    const nextStepId = useId();
    const completedId = useId();
    const emailId = useId();
    const phoneId = useId();
    const mounted = useRef(true);

    useEffect(() => {
        mounted.current = true;
        return () => {
            mounted.current = false;
        };
    }, []);

    // First field gets focus when edit mode opens.
    useEffect(() => {
        if (!editing) return;
        formRef.current?.querySelector<HTMLElement>("button, input, textarea, select")?.focus();
    }, [editing]);

    const saved = entry ?? defaultCallHistoryWorkflow(call);
    const callLabel = call.phone_number || call.from_number || "this call";
    const accent = leadTypeAccent(saved.leadType);

    const startEdit = () => {
        setDraft(draftFrom(call, entry));
        setProblems({});
        setSaveError(null);
        setEditing(true);
    };
    const cancel = () => {
        if (saving) return;
        setDraft(draftFrom(call, entry));
        setProblems({});
        setSaveError(null);
        setEditing(false);
    };
    const change = (patch: Partial<Draft>) => setDraft((current) => ({ ...current, ...patch }));

    const save = async () => {
        if (saving) return;
        const base = draftFrom(call, entry);
        const emailChanged = draft.email.trim() !== base.email.trim();
        const phoneChanged = draft.phone.trim() !== base.phone.trim();
        const nextProblems = {
            email: emailChanged ? callerGaveProblem("email", draft.email) ?? undefined : undefined,
            phone: phoneChanged ? callerGaveProblem("phone", draft.phone) ?? undefined : undefined,
        };
        setProblems(nextProblems);
        if (nextProblems.email || nextProblems.phone) return;

        const patch: CallWorkflowPatch = {};
        if (draft.leadType !== base.leadType) patch.leadType = draft.leadType;
        if (draft.notes !== base.notes) patch.notes = draft.notes;
        if (
            draft.contact !== base.contact || draft.interest !== base.interest
            || draft.nextStep !== base.nextStep || draft.completed !== base.completed
        ) {
            patch.form = {
                contact: draft.contact,
                interest: draft.interest,
                nextStep: draft.nextStep,
                completed: draft.completed,
            };
        }
        const patchSent = Object.keys(patch).length > 0;
        const corrections: Array<{ kind: CallerGaveKind; label: string; value: string }> = [];
        if (emailChanged) corrections.push({ kind: "email", label: "the email the caller gave", value: draft.email });
        if (phoneChanged) corrections.push({ kind: "phone", label: "the number the caller gave", value: draft.phone });

        if (!patchSent && corrections.length === 0) {
            setEditing(false);
            setSaveError(null);
            return;
        }

        setSaving(true);
        setSaveError(null);
        if (patchSent) onWorkflowChange(call, patch);
        const done: string[] = [];
        const lines: string[] = [];
        for (const item of corrections) {
            try {
                await correctFn(call.id, item.kind, item.value);
                done.push(item.label);
            } catch (error) {
                const reason = error instanceof Error && error.message.trim() ? ` ${error.message.trim()}` : "";
                lines.push(`Not saved: ${item.label}.${reason}`);
            }
        }
        if (!mounted.current) return;
        setSaving(false);
        if (lines.length > 0) {
            const savedParts = [...(patchSent ? ["the notes and follow-up"] : []), ...done];
            if (savedParts.length > 0) lines.unshift(`Saved: ${savedParts.join(" and ")}.`);
            setSaveError(lines);
            if (savedParts.length > 0) onSaved();
            return;
        }
        setEditing(false);
        onSaved();
    };

    const failureBanner = failed ? (
        <div role="alert" className="mb-3 rounded-lg border border-border p-3 text-sm">
            Call details are not saved or could not load. Retry to sync your pending changes.
            <button type="button" className="ml-2 font-semibold underline" onClick={onRetry}>Retry sync</button>
        </div>
    ) : busy ? <p role="status" className="mb-3 text-sm text-muted-foreground">Syncing call details…</p> : null;

    return (
        <section className="content-card" aria-label="Notes and follow-up">
            <div className="mb-4 flex items-center justify-between gap-3">
                <h2 className="text-sm font-semibold text-foreground">Notes and follow-up</h2>
                {canEdit && !editing ? (
                    <Button type="button" variant="outline" size="sm" onClick={startEdit} className="gap-1.5 print:hidden">
                        <Pencil className="h-3.5 w-3.5" aria-hidden />
                        Edit
                    </Button>
                ) : null}
            </div>
            {failureBanner}

            {editing ? (
                <form
                    ref={formRef}
                    className="space-y-4"
                    onSubmit={(event) => {
                        event.preventDefault();
                        void save();
                    }}
                    onKeyDown={(event) => {
                        if (event.key === "Escape") {
                            event.stopPropagation();
                            cancel();
                        }
                    }}
                >
                    <fieldset disabled={saving} className="m-0 min-w-0 space-y-4 border-0 p-0">
                        <div className="space-y-1.5">
                            <span className={LABEL_CLASS}>Lead type</span>
                            <div>
                                <LeadTypeSelect value={draft.leadType} onChange={(leadType) => change({ leadType })} callLabel={callLabel} />
                            </div>
                        </div>
                        <div className="space-y-1.5">
                            <label htmlFor={notesId} className={LABEL_CLASS}>Notes</label>
                            <textarea id={notesId} rows={4} maxLength={4000} value={draft.notes} placeholder="Add a note…"
                                onChange={(event) => change({ notes: event.target.value })} className={`${INPUT_CLASS} resize-y`} />
                        </div>
                        <div className="space-y-1.5">
                            <label htmlFor={contactId} className={LABEL_CLASS}>Contact or decision maker</label>
                            <input id={contactId} maxLength={160} value={draft.contact} placeholder="Who did the agent speak with?"
                                onChange={(event) => change({ contact: event.target.value })} className={INPUT_CLASS} />
                        </div>
                        <div className="space-y-1.5">
                            <label htmlFor={interestId} className={LABEL_CLASS}>Key need or interest</label>
                            <input id={interestId} maxLength={1000} value={draft.interest} placeholder="Main need, interest, or objection"
                                onChange={(event) => change({ interest: event.target.value })} className={INPUT_CLASS} />
                        </div>
                        <div className="space-y-1.5">
                            <label htmlFor={nextStepId} className={LABEL_CLASS}>Next step</label>
                            <input id={nextStepId} maxLength={1000} value={draft.nextStep} placeholder="Follow-up action, owner, or timing"
                                onChange={(event) => change({ nextStep: event.target.value })} className={INPUT_CLASS} />
                        </div>
                        <label htmlFor={completedId} className="flex items-center gap-2 text-sm text-foreground">
                            <input id={completedId} type="checkbox" checked={draft.completed}
                                onChange={(event) => change({ completed: event.target.checked })} />
                            Follow-up form completed
                        </label>
                        <div className="space-y-1.5">
                            <label htmlFor={emailId} className={LABEL_CLASS}>Email the caller gave</label>
                            <input id={emailId} type="text" inputMode="email" autoComplete="off" value={draft.email} placeholder="Not provided"
                                aria-invalid={problems.email ? true : undefined} aria-describedby={problems.email ? `${emailId}-problem` : undefined}
                                onChange={(event) => change({ email: event.target.value })} className={INPUT_CLASS} />
                            {problems.email ? <p id={`${emailId}-problem`} role="alert" className="text-xs text-destructive">{problems.email}</p> : null}
                        </div>
                        <div className="space-y-1.5">
                            <label htmlFor={phoneId} className={LABEL_CLASS}>Number the caller gave</label>
                            <input id={phoneId} type="text" inputMode="tel" autoComplete="off" value={draft.phone} placeholder="Not provided"
                                aria-invalid={problems.phone ? true : undefined} aria-describedby={problems.phone ? `${phoneId}-problem` : undefined}
                                onChange={(event) => change({ phone: event.target.value })} className={INPUT_CLASS} />
                            {problems.phone ? <p id={`${phoneId}-problem`} role="alert" className="text-xs text-destructive">{problems.phone}</p> : null}
                        </div>
                    </fieldset>
                    {saveError ? (
                        <div role="alert" className="space-y-1 rounded-lg border border-destructive/40 p-3 text-sm text-destructive">
                            <p className="font-semibold">{NOT_SAVED_SENTENCE}</p>
                            {saveError.map((line) => <p key={line}>{line}</p>)}
                        </div>
                    ) : null}
                    <div className="flex items-center gap-2">
                        <Button type="submit" size="sm" disabled={saving}>{saving ? "Saving…" : "Save"}</Button>
                        <Button type="button" variant="outline" size="sm" disabled={saving} onClick={cancel}>Cancel</Button>
                    </div>
                </form>
            ) : (
                <>
                    <dl className="space-y-3">
                        <div>
                            <dt className="text-[11px] font-bold uppercase tracking-wide text-muted-foreground">Lead type</dt>
                            <dd className="mt-0.5 flex items-center gap-2 text-sm font-semibold text-foreground">
                                <span aria-hidden className={`h-2 w-2 rounded-full ${accent.dot}`} />
                                {accent.label}
                                {entry ? null : <span className="font-normal text-muted-foreground">(suggested)</span>}
                            </dd>
                        </div>
                        <ReadRow label="Notes" value={saved.notes.trim()} empty="No notes yet" />
                        <ReadRow label="Contact or decision maker" value={saved.form.contact.trim()} empty="Not filled in" />
                        <ReadRow label="Key need or interest" value={saved.form.interest.trim()} empty="Not filled in" />
                        <ReadRow label="Next step" value={saved.form.nextStep.trim()} empty="Not filled in" />
                        <ReadRow label="Follow-up form" value={saved.form.completed ? "Marked complete" : "Not marked complete"} empty="" />
                    </dl>
                    {canEdit ? null : <p className="mt-3 text-xs text-muted-foreground">{READ_ONLY_SENTENCE}</p>}
                </>
            )}
        </section>
    );
}
