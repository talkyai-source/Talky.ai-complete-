"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { AttachmentPicker } from "@/components/support/attachment-picker";
import { hasUploadInFlight, type PickedAttachment } from "@/lib/support-attachments";
import { validateTicketDraft, DESCRIPTION_MAX, SUBJECT_MAX, type TicketDraft, type TicketDraftErrors } from "@/lib/support-ticket-draft";
import type { ExistingTicket, SupportConfig } from "@/lib/support-api";
import { cn } from "@/lib/utils";

export const TEXTAREA_CLASS = "flex w-full rounded-md border border-input dark:border-foreground/20 bg-background dark:bg-accent/20 px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50";

function RelatedChip({ label, href, onRemove, disabled }: { label: string; href: string; onRemove: () => void; disabled?: boolean }) {
    return (
        <span className="inline-flex max-w-full items-center gap-1 rounded-full border border-border bg-background/70 px-2.5 py-0.5 text-xs">
            <Link href={href} className="truncate underline-offset-2 hover:underline">{label}</Link>
            <button type="button" onClick={onRemove} disabled={disabled} aria-label={`Remove ${label}`} className="rounded p-0.5 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                <X className="h-3 w-3" aria-hidden />
            </button>
        </span>
    );
}

/**
 * The ticket form shown when the assistant hands over. Everything is
 * prefilled from the server's draft and editable; the related campaign or
 * call can be removed; the submit is only enabled once every upload finished.
 */
export function TicketForm({ config, draft, onDraftChange, attachments, onAttachmentsChange, existingTicket, existingChoice, onChooseExisting, onSubmit, onAppend, busy, error, submitLabel = "Send to support" }: {
    config: SupportConfig;
    draft: TicketDraft;
    onDraftChange: (draft: TicketDraft) => void;
    attachments: PickedAttachment[];
    onAttachmentsChange: (update: (current: PickedAttachment[]) => PickedAttachment[]) => void;
    existingTicket: ExistingTicket | null;
    existingChoice: "append" | "new" | null;
    onChooseExisting: (choice: "append" | "new") => void;
    onSubmit: () => void;
    onAppend: () => void;
    busy: boolean;
    error: string | null;
    submitLabel?: string;
}) {
    const id = useId();
    const [touched, setTouched] = useState(false);
    const errors: TicketDraftErrors = touched ? validateTicketDraft(draft, config) : {};
    const uploading = hasUploadInFlight(attachments);
    const appending = existingTicket !== null && existingChoice === "append";

    const submit = (event: React.FormEvent) => {
        event.preventDefault();
        setTouched(true);
        if (Object.keys(validateTicketDraft(draft, config)).length) return;
        if (appending) onAppend();
        else onSubmit();
    };

    if (existingTicket && existingChoice === null) {
        return (
            <div className="flex flex-col gap-3 rounded-xl border border-border bg-background/70 p-3" role="group" aria-labelledby={`${id}-existing`}>
                <p id={`${id}-existing`} className="text-sm font-medium">You already have an open ticket in this category.</p>
                <p className="text-sm text-muted-foreground"><span className="font-mono">{existingTicket.number}</span> · {existingTicket.subject}</p>
                <div className="flex flex-wrap gap-2">
                    <Button type="button" size="sm" onClick={() => onChooseExisting("append")}>Add this to {existingTicket.number} instead</Button>
                    <Button type="button" size="sm" variant="outline" onClick={() => onChooseExisting("new")}>Create a new ticket</Button>
                </div>
            </div>
        );
    }

    return (
        <form onSubmit={submit} noValidate className="flex min-w-0 flex-col gap-3" aria-busy={busy} data-support-ticket-form>
            {appending && existingTicket ? (
                <p className="text-sm text-muted-foreground">
                    This will be added as a message on <span className="font-mono">{existingTicket.number}</span>. <button type="button" className="underline underline-offset-2" onClick={() => onChooseExisting("new")}>Create a new ticket instead</button>
                </p>
            ) : null}
            {!appending ? (
                <>
                    <div className="flex flex-col gap-1">
                        <Label htmlFor={`${id}-subject`}>Subject</Label>
                        <Input id={`${id}-subject`} value={draft.subject} maxLength={SUBJECT_MAX} disabled={busy} aria-invalid={!!errors.subject} aria-describedby={errors.subject ? `${id}-subject-error` : undefined}
                            onChange={(event) => onDraftChange({ ...draft, subject: event.target.value })} />
                        {errors.subject ? <p id={`${id}-subject-error`} className="text-xs text-red-600 dark:text-red-400">{errors.subject}</p> : null}
                    </div>
                    <div className="flex flex-col gap-1">
                        <Label htmlFor={`${id}-category`}>Category</Label>
                        <Select id={`${id}-category`} ariaLabel="Category" value={draft.category} disabled={busy} ariaInvalid={!!errors.category} onChange={(next) => onDraftChange({ ...draft, category: next })}>
                            {config.categories.map((category) => <option key={category.id} value={category.id}>{category.label}</option>)}
                        </Select>
                        {errors.category ? <p className="text-xs text-red-600 dark:text-red-400">{errors.category}</p> : null}
                    </div>
                </>
            ) : null}
            <div className="flex flex-col gap-1">
                <Label htmlFor={`${id}-description`}>{appending ? "Message" : "Description"}</Label>
                <textarea id={`${id}-description`} value={draft.description} rows={6} maxLength={DESCRIPTION_MAX} disabled={busy} aria-invalid={!!errors.description} aria-describedby={errors.description ? `${id}-description-error` : undefined}
                    className={cn(TEXTAREA_CLASS, "min-h-28 resize-y")} onChange={(event) => onDraftChange({ ...draft, description: event.target.value })} />
                {errors.description ? <p id={`${id}-description-error`} className="text-xs text-red-600 dark:text-red-400">{errors.description}</p> : null}
            </div>
            {!appending && (draft.relatedCampaignId || draft.relatedCallId) ? (
                <div className="flex flex-col gap-1">
                    <span className="text-sm font-medium">Related</span>
                    <div className="flex flex-wrap gap-1.5">
                        {draft.relatedCampaignId ? <RelatedChip label="Campaign" href={`/campaigns/${draft.relatedCampaignId}`} disabled={busy} onRemove={() => onDraftChange({ ...draft, relatedCampaignId: null })} /> : null}
                        {draft.relatedCallId ? <RelatedChip label="Call" href={`/calls/${draft.relatedCallId}`} disabled={busy} onRemove={() => onDraftChange({ ...draft, relatedCallId: null })} /> : null}
                    </div>
                </div>
            ) : null}
            <AttachmentPicker items={attachments} onChange={onAttachmentsChange} limits={config.attachments} disabled={busy} />
            {error ? <p role="alert" className="text-sm text-red-600 dark:text-red-400">{error}</p> : null}
            <div className="flex flex-wrap items-center gap-2">
                <Button type="submit" disabled={busy || uploading}>{appending && existingTicket ? `Add to ${existingTicket.number}` : submitLabel}</Button>
                {uploading ? <span className="text-xs text-muted-foreground">Waiting for uploads to finish…</span> : null}
            </div>
        </form>
    );
}
