"use client";

import { useId, useRef, useState, type DragEvent } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Modal } from "@/components/ui/modal";
import { cn } from "@/lib/utils";
import { useNotificationsActions } from "@/lib/notifications-client";

/**
 * Settings → Telephony: the "Buy Now" card and its documents window.
 *
 * Nothing here is sent anywhere. The chosen files are held in this window's
 * memory only and are dropped when it closes; Submit closes the window and
 * shows a message. There is no request of any kind.
 */

type DocumentGroup = { title: string; items: readonly [string, string] };

const BUSINESS: DocumentGroup = {
    title: "1. Business Use",
    items: ["A) Business registration", "B) Photo ID matching the business registration"],
};
const PERSONAL: DocumentGroup = {
    title: "2. Personal Use",
    items: ["A) Utility bill (proof of address)", "B) Photo ID matching the utility bill"],
};

/** One file. Same behaviour and words as the Contacts upload (app/contacts/page.tsx:753-808). */
function UploadBox({ itemId, file, onChange }: { itemId: string; file: File | null; onChange: (next: File | null) => void }) {
    const inputRef = useRef<HTMLInputElement>(null);
    const [dragActive, setDragActive] = useState(false);

    function handleDragOver(e: DragEvent<HTMLDivElement>) {
        e.preventDefault();
        e.stopPropagation();
        setDragActive(true);
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
                    <p data-file-name className="truncate text-sm font-medium text-foreground" title={file.name}>{file.name}</p>
                    <div className="mt-2 flex flex-wrap gap-2">
                        <Button type="button" variant="outline" size="sm" aria-describedby={itemId} onClick={() => inputRef.current?.click()}>
                            Choose another
                        </Button>
                        <Button type="button" variant="ghost" size="sm" aria-describedby={itemId} onClick={() => onChange(null)}>
                            Remove
                        </Button>
                    </div>
                </div>
            ) : (
                <button
                    type="button"
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
function DocumentCard({ group, onSubmit }: { group: DocumentGroup; onSubmit: () => void }) {
    const baseId = useId();
    const titleId = `${baseId}-title`;
    const [files, setFiles] = useState<[File | null, File | null]>([null, null]);

    return (
        <section data-document-card aria-labelledby={titleId} className="flex min-w-0 flex-col rounded-xl border border-border bg-card/50 p-4">
            <h3 id={titleId} className="text-sm font-semibold text-foreground">{group.title}</h3>
            <div className="mt-3 space-y-4">
                {group.items.map((text, index) => {
                    const itemId = `${baseId}-item-${index}`;
                    return (
                        <div key={text} data-document-item className="min-w-0">
                            <p id={itemId} className="text-sm text-foreground">{text}</p>
                            <UploadBox
                                itemId={itemId}
                                file={files[index]}
                                onChange={(next) => setFiles((current) => (index === 0 ? [next, current[1]] : [current[0], next]))}
                            />
                        </div>
                    );
                })}
            </div>
            <div className="mt-4 flex flex-1 items-end justify-end">
                <Button type="button" aria-describedby={titleId} disabled={!files[0] || !files[1]} onClick={onSubmit}>
                    Submit
                </Button>
            </div>
        </section>
    );
}

export function TelephonyBuyCard() {
    const { create: createNotification } = useNotificationsActions();
    const [open, setOpen] = useState(false);

    function handleSubmit() {
        setOpen(false);
        createNotification({ type: "success", title: "Thank you. Your documents have been submitted." });
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
                onOpenChange={setOpen}
                title="Documentation is required:"
                size="lg"
                footer={
                    <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
                        <Button type="button" variant="outline" onClick={() => setOpen(false)}>
                            Cancel
                        </Button>
                    </div>
                }
            >
                <div data-document-grid className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <DocumentCard group={BUSINESS} onSubmit={handleSubmit} />
                    <DocumentCard group={PERSONAL} onSubmit={handleSubmit} />
                </div>
            </Modal>
        </Card>
    );
}
