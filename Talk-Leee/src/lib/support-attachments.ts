import type { SupportConfig } from "@/lib/support-api";

/*
 * Attachment limits from GET /support/config, enforced before any upload.
 * The server decides the real type from the file's content; this only stops
 * a file that cannot pass from being sent at all.
 */

export type AttachmentLimits = SupportConfig["attachments"];

export type AttachmentStatus = "uploading" | "done" | "failed";

export type PickedAttachment = {
    localId: string;
    name: string;
    sizeBytes: number;
    status: AttachmentStatus;
    attachmentId: string | null;
    error: string | null;
};

export function attachmentExtension(name: string): string {
    const dot = name.lastIndexOf(".");
    return dot < 0 ? "" : name.slice(dot + 1).toLowerCase();
}

export function formatBytes(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Null when the file may be uploaded, otherwise the reason it may not. */
export function rejectAttachment(file: { name: string; size: number }, limits: AttachmentLimits, alreadyPicked: number): string | null {
    if (limits.max_per_message > 0 && alreadyPicked >= limits.max_per_message) {
        return `You can attach up to ${limits.max_per_message} files.`;
    }
    const extension = attachmentExtension(file.name);
    if (limits.types.length > 0 && !limits.types.includes(extension)) {
        return `${file.name} is not an accepted type. Accepted: ${limits.types.join(", ")}.`;
    }
    const maxBytes = limits.max_mb * 1024 * 1024;
    if (maxBytes > 0 && file.size > maxBytes) {
        return `${file.name} is ${formatBytes(file.size)}; the limit is ${limits.max_mb} MB.`;
    }
    if (file.size === 0) return `${file.name} is empty.`;
    return null;
}

export function acceptAttribute(limits: AttachmentLimits): string | undefined {
    return limits.types.length ? limits.types.map((type) => `.${type}`).join(",") : undefined;
}

export function uploadedAttachmentIds(items: PickedAttachment[]): string[] {
    return items.flatMap((item) => (item.status === "done" && item.attachmentId ? [item.attachmentId] : []));
}

export function hasUploadInFlight(items: PickedAttachment[]): boolean {
    return items.some((item) => item.status === "uploading");
}
