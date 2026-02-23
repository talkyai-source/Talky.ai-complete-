"use client";

import { useEffect, useMemo, useState } from "react";
import { Modal } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useCampaignContacts, useCampaigns, useSendEmail } from "@/lib/api-hooks";
import type { EmailTemplate } from "@/lib/models";
import { cn } from "@/lib/utils";
import { HtmlPreview } from "@/components/email/html-preview";
import { RichTextEditor } from "@/components/email/rich-text-editor";
import { isValidEmail, normalizeEmailList, splitEmailInput } from "@/lib/email-utils";

type Step = "recipients" | "template" | "edit" | "confirm";

function contactLabel(input: { first_name?: string; last_name?: string; email?: string }) {
    const name = [input.first_name, input.last_name].filter(Boolean).join(" ").trim();
    return name || input.email || "—";
}

function connectorTitle(reason?: string) {
    if (!reason) return "Email sending is blocked.";
    if (/expired/i.test(reason)) return "Email credentials expired.";
    if (/disconnected/i.test(reason)) return "Email connector disconnected.";
    return "Email sending is blocked.";
}

export function SendEmailModal({
    open,
    onOpenChange,
    templates,
    selectedTemplateId,
    connectorBlocked,
    connectorBlockReason,
}: {
    open: boolean;
    onOpenChange: (next: boolean) => void;
    templates: EmailTemplate[];
    selectedTemplateId?: string;
    connectorBlocked: boolean;
    connectorBlockReason?: string;
}) {
    const send = useSendEmail();
    const campaignsQ = useCampaigns();

    const [step, setStep] = useState<Step>("recipients");
    const [recipientsText, setRecipientsText] = useState("");
    const [subject, setSubject] = useState("");
    const [templateId, setTemplateId] = useState<string>(selectedTemplateId ?? templates[0]?.id ?? "");
    const [editingHtml, setEditingHtml] = useState<string>("");
    const [editEnabled, setEditEnabled] = useState(false);
    const [allowEditLocked, setAllowEditLocked] = useState(false);

    const [contactsOpen, setContactsOpen] = useState(false);
    const [contactsCampaignId, setContactsCampaignId] = useState<string>("");
    const [contactsSelected, setContactsSelected] = useState<Record<string, boolean>>({});

    const contactsQ = useCampaignContacts(contactsCampaignId || undefined, 1, 200);

    const template = useMemo(() => templates.find((t) => t.id === templateId), [templateId, templates]);

    useEffect(() => {
        if (!open) return;
        setStep("recipients");
        setRecipientsText("");
        setSubject("");
        setTemplateId(selectedTemplateId ?? templates[0]?.id ?? "");
        setEditingHtml("");
        setEditEnabled(false);
        setAllowEditLocked(false);
        setContactsOpen(false);
        setContactsCampaignId("");
        setContactsSelected({});
    }, [open, selectedTemplateId, templates]);

    useEffect(() => {
        if (!template) return;
        if (editingHtml) return;
        setEditingHtml(template.html);
    }, [template, editingHtml]);

    const recipients = useMemo(() => normalizeEmailList(splitEmailInput(recipientsText)), [recipientsText]);
    const invalidRecipients = useMemo(() => recipients.filter((e) => !isValidEmail(e)), [recipients]);
    const recipientsOk = recipients.length > 0 && invalidRecipients.length === 0;

    const templateOk = Boolean(templateId);

    const editBlockedByLock = Boolean(template?.locked) && !allowEditLocked;

    const canProceedRecipients = recipientsOk;
    const canProceedTemplate = templateOk;
    const canProceedEdit = !editEnabled || !editBlockedByLock;

    const canSubmit = recipientsOk && templateOk && !connectorBlocked && !send.isPending;

    const effectiveHtml = editEnabled ? editingHtml : template?.html ?? "";

    const confirmFooter = (
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="text-xs text-gray-300">
                {connectorBlocked ? connectorBlockReason : null}
                {send.isError ? (connectorBlocked ? " • " : "") + "Send failed. Review details and try again." : null}
            </div>
            <div className="flex justify-end gap-2">
                <Button type="button" variant="ghost" onClick={() => setStep("edit")} disabled={send.isPending}>
                    Back
                </Button>
                <Button
                    type="button"
                    onClick={async () => {
                        if (!templateId) return;
                        await send.mutateAsync({
                            to: recipients,
                            templateId,
                            subject: subject.trim() ? subject.trim() : undefined,
                            html: editEnabled ? editingHtml : undefined,
                        });
                        onOpenChange(false);
                    }}
                    disabled={!canSubmit}
                >
                    {send.isPending ? "Sending…" : "Send email"}
                </Button>
            </div>
        </div>
    );

    const footer = (
        <div className="flex items-center justify-between">
            <Button
                type="button"
                variant="ghost"
                onClick={() => {
                    if (step === "recipients") onOpenChange(false);
                    else if (step === "template") setStep("recipients");
                    else if (step === "edit") setStep("template");
                    else setStep("edit");
                }}
                disabled={send.isPending}
            >
                {step === "recipients" ? "Close" : "Back"}
            </Button>
            {step === "confirm" ? null : (
                <Button
                    type="button"
                    onClick={() => {
                        if (step === "recipients" && canProceedRecipients) setStep("template");
                        else if (step === "template" && canProceedTemplate) setStep(editEnabled ? "edit" : "confirm");
                        else if (step === "edit" && canProceedEdit) setStep("confirm");
                    }}
                    disabled={
                        (step === "recipients" && !canProceedRecipients) ||
                        (step === "template" && !canProceedTemplate) ||
                        (step === "edit" && !canProceedEdit)
                    }
                >
                    Next
                </Button>
            )}
        </div>
    );

    return (
        <>
            <Modal
                open={open}
                onOpenChange={onOpenChange}
                size="xl"
                title="Send email"
                description="Select recipients, choose a template, and send."
                footer={step === "confirm" ? confirmFooter : footer}
            >
                <div className="space-y-5">
                    {connectorBlocked ? (
                        <div className="rounded-2xl border border-amber-500/20 bg-amber-500/10 p-4 text-sm text-amber-100">
                            <div className="font-semibold">{connectorTitle(connectorBlockReason)}</div>
                            <div className="mt-1 text-amber-100/80">{connectorBlockReason ?? "Email sending is currently blocked."}</div>
                        </div>
                    ) : null}
                    <div className="flex flex-wrap items-center gap-2 text-xs">
                        {(["recipients", "template", "edit", "confirm"] as Step[]).map((s) => (
                            <div
                                key={s}
                                className={cn(
                                    "rounded-full border px-3 py-1 font-semibold capitalize",
                                    step === s ? "border-white/20 bg-white/10 text-white" : "border-white/10 bg-white/5 text-gray-300"
                                )}
                            >
                                {s === "recipients" ? "Recipients" : s === "template" ? "Template" : s === "edit" ? "Edit" : "Confirm"}
                            </div>
                        ))}
                    </div>

                    {step === "recipients" ? (
                        <div className="space-y-3">
                            <div className="space-y-2">
                                <Label htmlFor="recipients">Recipients</Label>
                                <textarea
                                    id="recipients"
                                    value={recipientsText}
                                    onChange={(e) => setRecipientsText(e.target.value)}
                                    placeholder="name@company.com, other@company.com"
                                    rows={4}
                                    className={cn(
                                        "flex w-full rounded-lg border bg-white/5 px-3 py-2 text-sm text-white placeholder:text-gray-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/20 disabled:cursor-not-allowed disabled:opacity-50",
                                        invalidRecipients.length > 0 ? "border-red-500/40" : "border-white/10"
                                    )}
                                />
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                    <div className="text-xs text-gray-300">
                                        {recipients.length > 0 ? `${recipients.length} recipient${recipients.length === 1 ? "" : "s"}` : "Enter one or more email addresses."}
                                    </div>
                                    <Button type="button" variant="secondary" size="sm" onClick={() => setContactsOpen(true)}>
                                        Add from contacts
                                    </Button>
                                </div>
                                {invalidRecipients.length > 0 ? (
                                    <div className="text-xs text-red-200">
                                        Invalid: {invalidRecipients.slice(0, 5).join(", ")}
                                        {invalidRecipients.length > 5 ? "…" : null}
                                    </div>
                                ) : null}
                            </div>
                        </div>
                    ) : null}

                    {step === "template" ? (
                        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[360px_1fr]">
                            <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
                                <div className="space-y-2">
                                    <Label htmlFor="subject">Subject</Label>
                                    <Input
                                        id="subject"
                                        value={subject}
                                        onChange={(e) => setSubject(e.target.value)}
                                        placeholder="Optional subject"
                                        className="border-white/10 bg-white/5 text-white placeholder:text-gray-400 focus-visible:ring-white/20"
                                    />
                                </div>
                                <div className="mt-4 space-y-2">
                                    <Label htmlFor="template">Template</Label>
                                    <select
                                        id="template"
                                        value={templateId}
                                        onChange={(e) => {
                                            setTemplateId(e.target.value);
                                            setEditingHtml("");
                                            setAllowEditLocked(false);
                                        }}
                                        className="h-10 w-full rounded-md border border-white/10 bg-white/5 px-2 text-sm text-white"
                                    >
                                        {templates.map((t) => (
                                            <option key={t.id} value={t.id}>
                                                {t.name}
                                            </option>
                                        ))}
                                    </select>
                                </div>
                                <div className="mt-4 flex items-center justify-between gap-3 rounded-xl border border-white/10 bg-white/5 px-3 py-3">
                                    <div className="min-w-0">
                                        <div className="text-sm font-semibold text-white">Edit content</div>
                                        <div className="mt-1 text-xs text-gray-300">Optional. Some templates may be locked.</div>
                                    </div>
                                    <button
                                        type="button"
                                        className={cn(
                                            "inline-flex h-6 w-11 items-center rounded-full border transition-colors",
                                            editEnabled ? "bg-emerald-500/20 border-emerald-500/30" : "bg-white/5 border-white/10",
                                            connectorBlocked ? "opacity-60" : ""
                                        )}
                                        onClick={() => {
                                            if (editEnabled) {
                                                setEditEnabled(false);
                                                return;
                                            }
                                            setEditEnabled(true);
                                        }}
                                        aria-pressed={editEnabled}
                                    >
                                        <span
                                            className={cn(
                                                "ml-0.5 inline-block h-5 w-5 rounded-full bg-white transition-transform",
                                                editEnabled ? "translate-x-5" : "translate-x-0"
                                            )}
                                        />
                                    </button>
                                </div>
                            </div>
                            <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
                                <div className="text-sm font-semibold text-white">Preview</div>
                                <div className="mt-3 h-[420px]">
                                    <HtmlPreview html={template?.html ?? ""} />
                                </div>
                            </div>
                        </div>
                    ) : null}

                    {step === "edit" ? (
                        <div className="space-y-4">
                            <div className="flex items-center justify-between gap-3">
                                <div className="min-w-0">
                                    <div className="text-sm font-semibold text-white">Edit content</div>
                                    <div className="mt-1 text-xs text-gray-300">Changes apply only to this send.</div>
                                </div>
                                {template?.locked && !allowEditLocked ? (
                                    <Button type="button" variant="secondary" onClick={() => setAllowEditLocked(true)}>
                                        Unlock for this send
                                    </Button>
                                ) : null}
                            </div>

                            {template?.locked && !allowEditLocked ? (
                                <div className="rounded-2xl border border-amber-500/20 bg-amber-500/10 p-4 text-sm text-amber-100">
                                    This template is locked. Unlock editing to proceed.
                                </div>
                            ) : (
                                <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                                    <RichTextEditor html={editingHtml} onChange={setEditingHtml} disabled={connectorBlocked} />
                                    <div className="rounded-2xl border border-white/10 bg-white/5 p-3">
                                        <div className="text-sm font-semibold text-white">Live preview</div>
                                        <div className="mt-3 h-[320px]">
                                            <HtmlPreview html={editingHtml} />
                                        </div>
                                    </div>
                                </div>
                            )}
                        </div>
                    ) : null}

                    {step === "confirm" ? (
                        <div className="space-y-4">
                            <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
                                <div className="text-sm font-semibold text-white">Recipients</div>
                                <div className="mt-2 text-sm text-gray-200 break-words">{recipients.join(", ")}</div>
                            </div>
                            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                                <div className="space-y-4">
                                    <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
                                        <div className="text-sm font-semibold text-white">Template</div>
                                        <div className="mt-2 text-sm text-gray-200">{template?.name ?? "—"}</div>
                                    </div>
                                    <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
                                        <div className="text-sm font-semibold text-white">Subject</div>
                                        <div className="mt-2 text-sm text-gray-200">{subject.trim() ? subject.trim() : "—"}</div>
                                    </div>
                                    <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
                                        <div className="text-sm font-semibold text-white">Content</div>
                                        <div className="mt-2 text-sm text-gray-300">{editEnabled ? "Edited for this send" : "From template"}</div>
                                    </div>
                                </div>
                                <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
                                    <div className="text-sm font-semibold text-white">Preview</div>
                                    <div className="mt-3 h-[420px]">
                                        <HtmlPreview html={effectiveHtml} />
                                    </div>
                                </div>
                            </div>
                        </div>
                    ) : null}
                </div>
            </Modal>

            <Modal
                open={contactsOpen}
                onOpenChange={setContactsOpen}
                title="Select contacts"
                description="Add email addresses from your contacts."
                size="lg"
                footer={
                    <div className="flex items-center justify-between">
                        <Button type="button" variant="ghost" onClick={() => setContactsOpen(false)}>
                            Close
                        </Button>
                        <Button
                            type="button"
                            onClick={() => {
                                const emails = (contactsQ.data?.items ?? [])
                                    .filter((c) => c.email && contactsSelected[c.id])
                                    .map((c) => c.email as string);
                                const next = normalizeEmailList([...recipients, ...emails]);
                                setRecipientsText(next.join(", "));
                                setContactsOpen(false);
                            }}
                            disabled={!Object.values(contactsSelected).some(Boolean)}
                        >
                            Add selected
                        </Button>
                    </div>
                }
            >
                <div className="space-y-4">
                    <div className="space-y-2">
                        <Label htmlFor="contactsCampaign">Campaign</Label>
                        <select
                            id="contactsCampaign"
                            value={contactsCampaignId}
                            onChange={(e) => {
                                setContactsCampaignId(e.target.value);
                                setContactsSelected({});
                            }}
                            className="h-10 w-full rounded-md border border-white/10 bg-white/5 px-2 text-sm text-white"
                        >
                            <option value="">Select campaign…</option>
                            {(campaignsQ.data ?? []).map((c) => (
                                <option key={c.id} value={c.id}>
                                    {c.name}
                                </option>
                            ))}
                        </select>
                    </div>

                    {!contactsCampaignId ? (
                        <div className="rounded-xl border border-white/10 bg-white/5 p-4 text-sm text-gray-300">Pick a campaign to load contacts.</div>
                    ) : contactsQ.isLoading ? (
                        <div className="rounded-xl border border-white/10 bg-white/5 p-4 text-sm text-gray-300">Loading contacts…</div>
                    ) : contactsQ.isError ? (
                        <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-100">Could not load contacts.</div>
                    ) : (
                        <div className="max-h-[360px] overflow-y-auto rounded-xl border border-white/10 bg-white/5">
                            <div className="divide-y divide-white/10">
                                {(contactsQ.data?.items ?? [])
                                    .filter((c) => Boolean(c.email))
                                    .map((c) => (
                                        <label key={c.id} className="flex items-center gap-3 px-4 py-3">
                                            <input
                                                type="checkbox"
                                                checked={Boolean(contactsSelected[c.id])}
                                                onChange={(e) => setContactsSelected((p) => ({ ...p, [c.id]: e.target.checked }))}
                                            />
                                            <div className="min-w-0">
                                                <div className="truncate text-sm font-semibold text-white">{contactLabel(c)}</div>
                                                <div className="truncate text-xs text-gray-300">{c.email}</div>
                                            </div>
                                        </label>
                                    ))}
                            </div>
                        </div>
                    )}
                </div>
            </Modal>
        </>
    );
}
