"use client";

import { useId, useState } from "react";
import Link from "next/link";
import {
    CheckCircle2,
    ChevronDown,
    ClipboardCheck,
    Phone,
    StickyNote,
} from "lucide-react";

import { QuickReviewButtons } from "@/components/calls/quick-review-buttons";
import { Modal } from "@/components/ui/modal";
import type { Call } from "@/lib/dashboard-api";
import type {
    CallHistoryFormData,
    CallHistoryLeadType,
} from "@/lib/call-history-workflow";
import { isCallHistoryFormComplete } from "@/lib/call-history-workflow";

const LEAD_TYPE_STYLES: Record<
    CallHistoryLeadType,
    { label: string; dot: string; control: string }
> = {
    cold: {
        label: "Cold",
        dot: "bg-red-500",
        control: "border-red-500/35 bg-red-500/10 text-red-700 dark:text-red-300",
    },
    warm: {
        label: "Warm",
        dot: "bg-orange-500",
        control: "border-orange-500/40 bg-orange-500/10 text-orange-700 dark:text-orange-300",
    },
    hot: {
        label: "Hot",
        dot: "bg-emerald-500",
        control: "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
    },
    follow_up: {
        label: "Follow-up",
        dot: "bg-sky-500",
        control: "border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-300",
    },
};

export function LeadTypeSelect({
    value,
    onChange,
    callLabel,
}: {
    value: CallHistoryLeadType;
    onChange: (value: CallHistoryLeadType) => void;
    callLabel: string;
}) {
    const selected = LEAD_TYPE_STYLES[value];
    return (
        <div className="relative min-w-[7.5rem]">
            <span
                aria-hidden
                className={`pointer-events-none absolute left-2.5 top-1/2 z-10 h-2 w-2 -translate-y-1/2 rounded-full ${selected.dot}`}
            />
            <select
                value={value}
                onChange={(event) => onChange(event.target.value as CallHistoryLeadType)}
                aria-label={`Lead type for ${callLabel}`}
                className={`h-9 w-full appearance-none rounded-lg border py-1.5 pl-6 pr-7 text-xs font-semibold outline-none transition-[background-color,border-color,box-shadow] focus-visible:ring-2 focus-visible:ring-ring/40 ${selected.control}`}
            >
                {(Object.keys(LEAD_TYPE_STYLES) as CallHistoryLeadType[]).map((leadType) => (
                    <option key={leadType} value={leadType} className="bg-background text-foreground">
                        {LEAD_TYPE_STYLES[leadType].label}
                    </option>
                ))}
            </select>
            <ChevronDown
                aria-hidden
                className="pointer-events-none absolute right-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 opacity-70"
            />
        </div>
    );
}

export function CallHistoryNotesField({
    value,
    onChange,
    callLabel,
}: {
    value: string;
    onChange: (value: string) => void;
    callLabel: string;
}) {
    return (
        <label className="relative block min-w-0">
            <span className="sr-only">Notes for {callLabel}</span>
            <StickyNote
                aria-hidden
                className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
            />
            <input
                type="text"
                value={value}
                maxLength={4000}
                onChange={(event) => onChange(event.target.value)}
                placeholder="Add a note…"
                className="h-9 w-full min-w-0 rounded-lg border border-border bg-muted/20 py-1.5 pl-8 pr-2.5 text-xs text-foreground outline-none transition-[background-color,border-color,box-shadow] placeholder:text-muted-foreground hover:bg-background focus:border-ring/50 focus:bg-background focus:ring-2 focus:ring-ring/30"
            />
        </label>
    );
}

export function CallHistoryFormButton({
    value,
    onChange,
    callLabel,
    showLabel = false,
}: {
    value: CallHistoryFormData;
    onChange: (value: CallHistoryFormData) => void;
    callLabel: string;
    showLabel?: boolean;
}) {
    const [open, setOpen] = useState(false);
    const [draft, setDraft] = useState<CallHistoryFormData>(value);
    const [justCompleted, setJustCompleted] = useState(false);
    const formId = useId();
    const contactId = useId();
    const interestId = useId();
    const nextStepId = useId();

    const setOpenState = (next: boolean) => {
        if (next) {
            setDraft(value);
            setJustCompleted(false);
        }
        setOpen(next);
    };

    const updateDraft = (patch: Partial<CallHistoryFormData>) => {
        setJustCompleted(false);
        setDraft((current) => ({ ...current, ...patch, completed: false }));
    };

    const completeForm = (event: React.FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        if (!isCallHistoryFormComplete(draft)) return;
        const completed = { ...draft, completed: true };
        setDraft(completed);
        onChange(completed);
        setJustCompleted(true);
    };

    return (
        <>
            <button
                type="button"
                onClick={() => setOpenState(true)}
                aria-label={value.completed ? `Completed form for ${callLabel}` : `Open form for ${callLabel}`}
                title={value.completed ? "Form complete" : "Open post-call form"}
                className={`inline-flex ${showLabel ? "h-11 px-3" : "h-8 w-8"} items-center justify-center gap-1.5 rounded-lg border text-xs font-semibold transition-[background-color,border-color,color,box-shadow] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 ${value.completed
                    ? "border-emerald-500/50 bg-emerald-500/12 text-emerald-700 shadow-sm dark:text-emerald-300"
                    : "border-border bg-background text-muted-foreground hover:bg-accent hover:text-foreground"
                    }`}
            >
                {value.completed ? <CheckCircle2 className="h-4 w-4" aria-hidden /> : <ClipboardCheck className="h-4 w-4" aria-hidden />}
                {showLabel ? (value.completed ? "Form complete" : "Form") : null}
            </button>

            <Modal
                open={open}
                onOpenChange={setOpenState}
                title="Post-call form"
                description={`Capture the key details from the AI script for ${callLabel}.`}
                size="sm"
                footer={
                    <div className="flex items-center justify-end gap-2">
                        <button
                            type="button"
                            onClick={() => setOpenState(false)}
                            className="h-9 rounded-lg border border-border px-3 text-sm font-semibold text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                        >
                            Close
                        </button>
                        <button
                            type="submit"
                            form={formId}
                            className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-emerald-600 px-3 text-sm font-semibold text-white transition-colors hover:bg-emerald-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500 focus-visible:ring-offset-2"
                        >
                            <CheckCircle2 className="h-4 w-4" aria-hidden />
                            Complete form
                        </button>
                    </div>
                }
            >
                <form id={formId} onSubmit={completeForm} className="space-y-4">
                    {justCompleted ? (
                        <div role="status" className="flex items-start gap-2 rounded-xl border border-emerald-500/35 bg-emerald-500/10 p-3 text-sm text-emerald-800 dark:text-emerald-200">
                            <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
                            <div>
                                <p className="font-semibold">Form completed</p>
                                <p className="mt-0.5 text-xs opacity-85">The form button is now green, so completed calls are easy to spot.</p>
                            </div>
                        </div>
                    ) : null}

                    <div className="space-y-1.5">
                        <label htmlFor={contactId} className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                            Contact or decision maker
                        </label>
                        <input
                            id={contactId}
                            required
                            maxLength={160}
                            value={draft.contact}
                            onChange={(event) => updateDraft({ contact: event.target.value })}
                            placeholder="Who did the agent speak with?"
                            className="h-10 w-full rounded-lg border border-border bg-background px-3 text-sm text-foreground outline-none placeholder:text-muted-foreground focus:border-ring/50 focus:ring-2 focus:ring-ring/30"
                        />
                    </div>

                    <div className="space-y-1.5">
                        <label htmlFor={interestId} className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                            Key need or interest
                        </label>
                        <textarea
                            id={interestId}
                            required
                            rows={3}
                            maxLength={1000}
                            value={draft.interest}
                            onChange={(event) => updateDraft({ interest: event.target.value })}
                            placeholder="Summarize the main need, interest, or objection."
                            className="w-full resize-y rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none placeholder:text-muted-foreground focus:border-ring/50 focus:ring-2 focus:ring-ring/30"
                        />
                    </div>

                    <div className="space-y-1.5">
                        <label htmlFor={nextStepId} className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                            Next step
                        </label>
                        <textarea
                            id={nextStepId}
                            required
                            rows={3}
                            maxLength={1000}
                            value={draft.nextStep}
                            onChange={(event) => updateDraft({ nextStep: event.target.value })}
                            placeholder="Add the follow-up action, owner, or timing."
                            className="w-full resize-y rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none placeholder:text-muted-foreground focus:border-ring/50 focus:ring-2 focus:ring-ring/30"
                        />
                    </div>
                </form>
            </Modal>
        </>
    );
}

export function CallReviewModal({
    call,
    onClose,
}: {
    call: Call | null;
    onClose: () => void;
}) {
    const [saved, setSaved] = useState(false);
    const callLabel = call?.phone_number || call?.from_number || "this call";

    return (
        <Modal
            open={Boolean(call)}
            onOpenChange={(next) => {
                if (!next) onClose();
            }}
            title="Call review"
            description={`How did the AI handle the call with ${callLabel}?`}
            size="sm"
            footer={
                <div className="flex items-center justify-between gap-3">
                    {call ? (
                        <Link
                            href={`/calls/${call.id}`}
                            className="text-sm font-semibold text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
                        >
                            Open call details
                        </Link>
                    ) : <span />}
                    <button
                        type="button"
                        onClick={onClose}
                        className="h-9 rounded-lg bg-primary px-4 text-sm font-semibold text-primary-foreground transition-opacity hover:opacity-90"
                    >
                        {saved ? "Done" : "Maybe later"}
                    </button>
                </div>
            }
        >
            {call ? (
                <div className="space-y-4">
                    <div className="flex items-center gap-3 rounded-xl border border-border bg-muted/30 p-3">
                        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-background shadow-sm">
                            <Phone className="h-4 w-4 text-muted-foreground" aria-hidden />
                        </div>
                        <div className="min-w-0">
                            <p className="truncate text-sm font-semibold text-foreground">{callLabel}</p>
                            <p className="text-xs text-muted-foreground">Choose thumbs up or thumbs down.</p>
                        </div>
                    </div>
                    <div className="flex justify-center py-1">
                        <QuickReviewButtons
                            callId={call.id}
                            touchFriendly
                            className="items-center"
                            onSaved={() => setSaved(true)}
                        />
                    </div>
                    {saved ? (
                        <p role="status" className="flex items-center justify-center gap-1.5 text-sm font-semibold text-emerald-700 dark:text-emerald-300">
                            <CheckCircle2 className="h-4 w-4" aria-hidden />
                            Review saved
                        </p>
                    ) : null}
                </div>
            ) : null}
        </Modal>
    );
}
