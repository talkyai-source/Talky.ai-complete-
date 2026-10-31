"use client";

import { useId, useRef, useState } from "react";
import { Loader2, Paperclip, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { supportApi } from "@/lib/support-api";
import { acceptAttribute, formatBytes, rejectAttachment, type AttachmentLimits, type PickedAttachment } from "@/lib/support-attachments";
import { cn } from "@/lib/utils";

/*
 * Picks files, checks them against the config's limits before anything is
 * sent, uploads each one and shows per-file status: uploading, done, failed.
 * The shared client exposes no upload progress, so there is no percentage.
 */
export function AttachmentPicker({ items, onChange, limits, disabled, className }: {
    items: PickedAttachment[];
    onChange: (update: (current: PickedAttachment[]) => PickedAttachment[]) => void;
    limits: AttachmentLimits;
    disabled?: boolean;
    className?: string;
}) {
    const inputId = useId();
    const inputRef = useRef<HTMLInputElement | null>(null);
    const [rejected, setRejected] = useState<string | null>(null);

    const pick = (files: FileList | null) => {
        if (!files || !files.length) return;
        const reasons: string[] = [];
        let picked = items.length;
        for (const file of Array.from(files)) {
            const reason = rejectAttachment(file, limits, picked);
            if (reason) { reasons.push(reason); continue; }
            picked += 1;
            const localId = crypto.randomUUID();
            onChange((current) => [...current, { localId, name: file.name, sizeBytes: file.size, status: "uploading", attachmentId: null, error: null }]);
            supportApi.uploadAttachment(file).then((result) => {
                onChange((current) => current.map((item) => item.localId === localId ? { ...item, status: "done", attachmentId: result.attachment_id, name: result.filename } : item));
            }).catch((error: unknown) => {
                const message = error instanceof Error ? error.message : "The file could not be uploaded.";
                onChange((current) => current.map((item) => item.localId === localId ? { ...item, status: "failed", error: message } : item));
            });
        }
        setRejected(reasons.length ? reasons.join(" ") : null);
        if (inputRef.current) inputRef.current.value = "";
    };

    const remove = (localId: string) => onChange((current) => current.filter((item) => item.localId !== localId));
    const limitText = [
        limits.max_per_message ? `up to ${limits.max_per_message} files` : null,
        limits.max_mb ? `${limits.max_mb} MB each` : null,
        limits.types.length ? limits.types.join(", ") : null,
    ].filter(Boolean).join(" · ");

    return (
        <div className={cn("flex min-w-0 flex-col gap-2", className)}>
            <div className="flex flex-wrap items-center gap-2">
                {/* The visible "Attach files" button opens this input; it stays out of
                    the tab order so the keyboard never lands on an invisible control. */}
                <input
                    ref={inputRef}
                    id={inputId}
                    type="file"
                    multiple
                    accept={acceptAttribute(limits)}
                    className="sr-only"
                    tabIndex={-1}
                    aria-label="Attach files"
                    disabled={disabled}
                    onChange={(event) => pick(event.target.files)}
                />
                <Button type="button" variant="outline" size="sm" disabled={disabled} onClick={() => inputRef.current?.click()} aria-describedby={`${inputId}-limits`}>
                    <Paperclip aria-hidden /> Attach files
                </Button>
                <span id={`${inputId}-limits`} className="text-xs text-muted-foreground">{limitText || "Attachments"}</span>
            </div>
            {rejected ? <p role="alert" className="text-xs text-red-600 dark:text-red-400">{rejected}</p> : null}
            {items.length ? (
                <ul className="flex flex-col gap-1" aria-label="Attached files">
                    {items.map((item) => (
                        <li key={item.localId} className="flex min-w-0 items-center gap-2 rounded-md border border-border bg-background/60 px-2 py-1 text-xs">
                            {item.status === "uploading" ? <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" aria-hidden /> : null}
                            <span className="min-w-0 flex-1 truncate" title={item.name}>{item.name}</span>
                            <span className="shrink-0 text-muted-foreground">{formatBytes(item.sizeBytes)}</span>
                            <span className={cn("shrink-0", item.status === "failed" ? "text-red-600 dark:text-red-400" : item.status === "done" ? "text-emerald-700 dark:text-emerald-300" : "text-muted-foreground")} role="status">
                                {item.status === "uploading" ? "Uploading" : item.status === "done" ? "Done" : `Failed${item.error ? `: ${item.error}` : ""}`}
                            </span>
                            <button
                                type="button"
                                onClick={() => remove(item.localId)}
                                disabled={disabled}
                                aria-label={`Remove ${item.name}`}
                                className="shrink-0 rounded p-0.5 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                            >
                                <X className="h-3.5 w-3.5" aria-hidden />
                            </button>
                        </li>
                    ))}
                </ul>
            ) : null}
        </div>
    );
}
