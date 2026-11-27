"use client";

import { useEffect, useId, useRef, useState, type DragEvent } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Modal } from "@/components/ui/modal";
import { cn } from "@/lib/utils";
import { useNotificationsActions, useNotificationsState } from "@/lib/notifications-client";
import {
    DEFAULT_NUMBER_REQUEST_CONFIG,
    NUMBER_REQUEST_APPROVED_MESSAGE,
    NUMBER_REQUEST_FIELDS,
    NUMBER_REQUEST_REJECTED_MESSAGE,
    getNumberRequestConfig,
    hashNumberRequestFiles,
    isNumberRequestKeyReused,
    numberRequestFailureMessage,
    readSavedNumberRequestKey,
    refuseNumberRequestFile,
    storeSavedNumberRequestKey,
    submitNumberRequest,
    type NumberRequestConfig,
    type NumberRequestUseType,
} from "@/lib/telephony-number-requests-api";

/**
 * Settings → Telephony: the "Buy Now" card and its documents window.
 *
 * The chosen files are held in this window's memory and are dropped when it
 * closes. Submit sends the two files of its card (POST /telephony/number-requests)
 * and shows the message only after the server has answered 201. A failure keeps
 * the files and shows the server's message inside that card; a retry with the
 * same files re-uses the same Idempotency-Key. That key is also kept for this
 * browser tab (sessionStorage: the key and a one-way hash of the files' details,
 * never a file name), so it survives leaving the Telephony tab and a reload.
 */

type DocumentGroup = { useType: NumberRequestUseType; title: string; items: readonly [string, string] };
type Pair = readonly [File, File];
/** What identifies a chosen file for a retry: a re-chosen copy of the same file has the same four values. */
const fingerprint = (f: File) => `${f.name}|${f.size}|${f.lastModified}|${f.type}`;
type Attempt = { prints: readonly [string, string]; key: string };
/** What a card shows after a Submit that did not end in the thank-you message: a failure, or the decision staff already took. */
type Notice = { tone: "error" | "status"; text: string };

const BUSINESS: DocumentGroup = {
    useType: "business",
    title: "1. Business Use",
    items: ["A) Business registration", "B) Photo ID matching the business registration"],
};
const PERSONAL: DocumentGroup = {
    useType: "personal",
    title: "2. Personal Use",
    items: ["A) Utility bill (proof of address)", "B) Photo ID matching the utility bill"],
};

/** One file. Same behaviour and words as the Contacts upload (app/contacts/page.tsx:753-808). */
function UploadBox({ itemId, file, accept, locked, onChange }: { itemId: string; file: File | null; accept: string; locked: boolean; onChange: (next: File | null) => void }) {
    const inputRef = useRef<HTMLInputElement>(null);
    const [dragActive, setDragActive] = useState(false);

    function handleDragOver(e: DragEvent<HTMLDivElement>) {
        e.preventDefault();
        e.stopPropagation();
        if (!locked) setDragActive(true);
    }

    function handleDragLeave(e: DragEvent<HTMLDivElement>) {
        e.preventDefault();
        e.stopPropagation();
        setDragActive(false);
    }

    function handleDrop(e: DragEvent<HTMLDivElement>) {
        e.preventDefault();
        e.stopPropagation();
        setDragActive(false);
        if (locked) return;
        const dropped = e.dataTransfer.files?.[0];
        if (dropped) onChange(dropped);
    }

    return (
        <div
            data-upload-box
            className={cn(
                "mt-2 min-w-0 rounded-xl border-2 border-dashed p-3 transition-[background-color,border-color] duration-150 ease-out",
                file || dragActive ? "border-ring/60 bg-background/60" : "border-border bg-background/50 hover:border-ring/50",
            )}
            onDragEnter={handleDragOver}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
        >
            {/* The visible buttons open this input; it stays out of the Tab order. */}
            <input
                ref={inputRef}
                type="file"
                accept={accept}
                disabled={locked}
                tabIndex={-1}
                className="sr-only"
                aria-labelledby={itemId}
                onChange={(e) => {
                    const chosen = e.target.files?.[0];
                    if (chosen) onChange(chosen);
                    // The file is kept in state, so the same file can be chosen again after Remove.
                    e.target.value = "";
                }}
            />
            {file ? (
                <div className="min-w-0">
                    {/* No title or aria-label here: Sentry's click breadcrumbs record those attributes, and a file name can identify a person. */}
                    <p data-file-name data-sentry-element="number-request-file" className="truncate text-sm font-medium text-foreground">{file.name}</p>
                    <div className="mt-2 flex flex-wrap gap-2">
                        <Button type="button" variant="outline" size="sm" disabled={locked} aria-describedby={itemId} onClick={() => inputRef.current?.click()}>
                            Choose another
                        </Button>
                        <Button type="button" variant="ghost" size="sm" disabled={locked} aria-describedby={itemId} onClick={() => onChange(null)}>
                            Remove
                        </Button>
                    </div>
                </div>
            ) : (
                <button
                    type="button"
                    disabled={locked}
                    onClick={() => inputRef.current?.click()}
                    aria-describedby={itemId}
                    className="w-full rounded-xl p-2 text-center text-sm font-medium text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                >
                    Click to upload or drag and drop
                </button>
            )}
        </div>
    );
}

/** One inner card: its two documents, each with its own upload box, then its own Submit. */
function DocumentCard({ group, config, locked, onSubmit }: {
    group: DocumentGroup;
    config: NumberRequestConfig;
    locked: boolean;
    onSubmit: (useType: NumberRequestUseType, files: Pair) => Promise<Notice | null>;
}) {
    const baseId = useId();
    const titleId = `${baseId}-title`;
    const [files, setFiles] = useState<[File | null, File | null]>([null, null]);
    const [fileErrors, setFileErrors] = useState<[string | null, string | null]>([null, null]);
    const [notice, setNotice] = useState<Notice | null>(null);
    // True only in the card whose Submit was pressed, while its request is being sent.
    const [pending, setPending] = useState(false);
    const sectionRef = useRef<HTMLElement>(null);
    const accept = [...config.accepted_extensions, ...config.accepted_types].join(",");
    const fields = NUMBER_REQUEST_FIELDS[group.useType];
    const errorRef = useRef<HTMLParagraphElement>(null);

    // After a Submit that ends in a message, put keyboard focus on that message.
    useEffect(() => {
        if (notice) errorRef.current?.focus();
    }, [notice]);

    function change(index: 0 | 1, next: File | null) {
        const refusal = next ? refuseNumberRequestFile(next, fields[index], config) : null;
        setNotice(null);
        setFileErrors((current) => (index === 0 ? [refusal, current[1]] : [current[0], refusal]));
        // A refused file is not taken; a file already chosen stays.
        if (refusal) return;
        setFiles((current) => (index === 0 ? [next, current[1]] : [current[0], next]));
    }

    async function send() {
        const [first, second] = files;
        if (!first || !second) return;
        setNotice(null);
        setPending(true);
        // The pressed Submit is about to be disabled: keep keyboard focus inside the window, on its panel.
        sectionRef.current?.closest<HTMLElement>('[role="dialog"]')?.focus({ preventScroll: true });
        try {
            const result = await onSubmit(group.useType, [first, second]);
            if (result) setNotice(result);
        } finally {
            setPending(false);
        }
    }

    return (
        <section ref={sectionRef} data-document-card aria-labelledby={titleId} aria-busy={locked} className="flex min-w-0 flex-col rounded-xl border border-border bg-card/50 p-4">
            <h3 id={titleId} className="text-sm font-semibold text-foreground">{group.title}</h3>
            <div className="mt-3 space-y-4">
                {group.items.map((text, index) => {
                    const itemId = `${baseId}-item-${index}`;
                    const at = index === 0 ? 0 : 1;
                    return (
                        <div key={text} data-document-item className="min-w-0">
                            <p id={itemId} className="text-sm text-foreground">{text}</p>
                            <UploadBox itemId={itemId} file={files[at]} accept={accept} locked={locked} onChange={(next) => change(at, next)} />
                            {fileErrors[at] ? <p role="alert" data-file-error className="mt-2 text-sm text-destructive">{fileErrors[at]}</p> : null}
                        </div>
                    );
                })}
            </div>
            {notice?.tone === "error" ? <p ref={errorRef} tabIndex={-1} role="alert" data-submit-error className="mt-4 text-sm text-destructive">{notice.text}</p> : null}
            {notice?.tone === "status" ? <p ref={errorRef} tabIndex={-1} role="status" data-submit-status className="mt-4 text-sm text-foreground">{notice.text}</p> : null}
            <div className="mt-4 flex flex-1 items-end justify-end">
                <Button type="button" aria-describedby={titleId} disabled={locked || !files[0] || !files[1]} onClick={() => void send()}>
                    {/* The spinner of the app's other buttons (components/auth/logout-button.tsx), on the pressed Submit only. */}
                    {pending ? <Loader2 data-submit-spinner className="h-4 w-4 animate-spin" aria-hidden /> : null}
                    Submit
                </Button>
            </div>
        </section>
    );
}

export function TelephonyBuyCard() {
    const { create: createNotification } = useNotificationsActions();
    // The signed-in person and account (the scope the notifications use). Null until it is known, or when browser storage is closed.
    const { scopeKey } = useNotificationsState();
    const [open, setOpen] = useState(false);
    const [config, setConfig] = useState<NumberRequestConfig>(DEFAULT_NUMBER_REQUEST_CONFIG);
    const [sending, setSending] = useState(false);
    // One remembered attempt per card, in memory only: the same two files (name, size, date, type) re-use its key,
    // a changed file makes a new one, and only a success or a decided request for that card clears it. Closing the window keeps it.
    // The same key is kept for this browser tab too (see remember below), so it survives a tab change and a reload.
    const [attempts, setAttempts] = useState<Partial<Record<NumberRequestUseType, Attempt>>>({});
    const sendingRef = useRef(false);

    // The server's limits are read when the window opens; the built-in ones stand until then, or if the call fails.
    useEffect(() => {
        if (!open) return;
        const controller = new AbortController();
        getNumberRequestConfig(controller.signal).then(setConfig, () => undefined);
        return () => controller.abort();
    }, [open]);

    function handleOpenChange(next: boolean) {
        if (!next && sendingRef.current) return;
        setOpen(next);
    }

    /** Returns what to show in the card, or null once the server has saved the documents. */
    async function handleSubmit(useType: NumberRequestUseType, files: Pair): Promise<Notice | null> {
        if (sendingRef.current) return null;
        sendingRef.current = true;
        setSending(true);
        const prints = [fingerprint(files[0]), fingerprint(files[1])] as const;
        // What is kept in the tab's storage beside the key: a one-way hash of the files' details. Null: memory only.
        let hash: string | null = null;
        /** Keeps the key for these two files, in memory and (when possible) for this browser tab; null forgets it in both. */
        const remember = (key: string | null) => {
            setAttempts((current) => ({ ...current, [useType]: key ? { prints, key } : undefined }));
            if (!scopeKey) return;
            if (!key) storeSavedNumberRequestKey(scopeKey, useType, null);
            else if (hash) storeSavedNumberRequestKey(scopeKey, useType, { key, files: hash });
        };
        try {
            hash = await hashNumberRequestFiles(files);
            const kept = attempts[useType];
            let key = kept && kept.prints[0] === prints[0] && kept.prints[1] === prints[1] ? kept.key : null;
            if (!key && scopeKey && hash) {
                const saved = readSavedNumberRequestKey(scopeKey, useType);
                if (saved && saved.files === hash) key = saved.key;
            }
            key ??= globalThis.crypto.randomUUID();
            remember(key);
            const [firstField, secondField] = NUMBER_REQUEST_FIELDS[useType];
            const send = (idempotencyKey: string) =>
                submitNumberRequest({ useType, files: { [firstField]: files[0], [secondField]: files[1] }, idempotencyKey });
            let answer;
            try {
                answer = await send(key);
            } catch (err) {
                // The server says this key belongs to other documents: forget it and send once more with a new key
                // (once only; whatever that second send answers is what the customer sees).
                if (!isNumberRequestKeyReused(err)) throw err;
                key = globalThis.crypto.randomUUID();
                remember(key);
                answer = await send(key);
            }
            // The kept key brought back an OLD request that staff have already decided. Nothing more is sent:
            // the key is forgotten, the files stay, and the card says what was decided.
            if (answer && (answer.status === "approved" || answer.status === "rejected")) {
                remember(null);
                return { tone: "status", text: answer.status === "approved" ? NUMBER_REQUEST_APPROVED_MESSAGE : NUMBER_REQUEST_REJECTED_MESSAGE };
            }
        } catch (err) {
            return { tone: "error", text: numberRequestFailureMessage(err) };
        } finally {
            sendingRef.current = false;
            setSending(false);
        }
        remember(null);
        setOpen(false);
        createNotification({ type: "success", title: "Thank you. Your documents have been submitted." });
        return null;
    }

    return (
        <Card data-telephony-buy-card>
            <CardContent className="space-y-4 p-6">
                <p className="text-sm font-medium text-foreground">Minute charges vary by destination. Local conditions apply.</p>
                {/* The neutral notice box the provider cards use (telephony-providers-section.tsx:253). */}
                <p className="rounded-md border border-border bg-muted/30 p-3 text-xs text-muted-foreground">
                    Info: Each destination has its own regulations, which may vary from region to region.
                </p>
                <Button type="button" onClick={() => setOpen(true)}>Buy Now</Button>
            </CardContent>
            {/* The window's content is mounted only while it is open, so closing it drops the chosen files. */}
            <Modal
                open={open}
                onOpenChange={handleOpenChange}
                title="Documentation is required:"
                description="To buy a phone number, we need the documents that destination regulations require. Choose business use or personal use and upload both documents."
                size="lg"
                footer={
                    <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
                        <Button type="button" variant="outline" disabled={sending} onClick={() => handleOpenChange(false)}>
                            Cancel
                        </Button>
                    </div>
                }
            >
                <div data-document-grid aria-busy={sending} className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <DocumentCard group={BUSINESS} config={config} locked={sending} onSubmit={handleSubmit} />
                    <DocumentCard group={PERSONAL} config={config} locked={sending} onSubmit={handleSubmit} />
                </div>
            </Modal>
        </Card>
    );
}
