import { z } from "zod";
import { sharedHttpClient } from "@/lib/api";
import { isApiClientError } from "@/lib/http-client";

// Settings -> Telephony -> Buy Now: the two documents of a number request.
// Contract: backend/app/api/v1/endpoints/telephony_number_requests.py and
// backend/app/domain/services/number_requests/{config,errors}.py.

export type NumberRequestUseType = "business" | "personal";

/** The two form fields of each use, in the order the server expects them (config.py USE_TYPES). */
export const NUMBER_REQUEST_FIELDS: Record<NumberRequestUseType, readonly [string, string]> = {
    business: ["business_registration", "business_photo_id"],
    personal: ["utility_bill", "personal_photo_id"],
};

/** The server's own name for each document (config.py KIND_LABELS); its refusal sentences start with it. */
export const NUMBER_REQUEST_LABELS: Record<string, string> = {
    business_registration: "Business registration",
    business_photo_id: "Photo ID matching the business registration",
    utility_bill: "Utility bill (proof of address)",
    personal_photo_id: "Photo ID matching the utility bill",
};

const configSchema = z.object({
    accepted_types: z.array(z.string()),
    accepted_extensions: z.array(z.string()),
    max_file_bytes: z.number().positive(),
});
export type NumberRequestConfig = z.infer<typeof configSchema>;

/** Used until the server's answer arrives, or if it never does (config.py ACCEPTED_TYPES, DEFAULT_MAX_FILE_BYTES). */
export const DEFAULT_NUMBER_REQUEST_CONFIG: NumberRequestConfig = {
    accepted_types: ["application/pdf", "image/jpeg", "image/png"],
    accepted_extensions: [".pdf", ".jpg", ".jpeg", ".png"],
    max_file_bytes: 10 * 1024 * 1024,
};

const documentSchema = z.object({
    kind: z.string(),
    file_name: z.string().nullable().optional(),
    content_type: z.string(),
    size_bytes: z.number(),
});
const requestSchema = z.object({
    id: z.string(),
    use_type: z.enum(["business", "personal"]),
    status: z.string(),
    submitted_at: z.string(),
    documents: z.array(documentSchema),
});
export type NumberRequest = z.infer<typeof requestSchema>;

/** The server's sentence when it could not save the documents (503 storage_unavailable). */
export const NUMBER_REQUEST_NOT_SAVED_MESSAGE = "Your documents could not be saved. Nothing was kept. Please try again.";
/** The server's sentence when a save may or may not have happened (503 save_unconfirmed). */
export const NUMBER_REQUEST_UNCONFIRMED_MESSAGE = "We could not confirm that your documents were saved. Please send the same files again.";
/** The server's own 401 sentence (backend dependencies.py), shown only when a 401 arrives with no message of its own. */
export const NUMBER_REQUEST_SESSION_ENDED_MESSAGE = "Your login session has ended. Please sign in again.";
/** The sentences of the two decision emails (backend notify.py), shown when a retry finds a request staff already decided. */
export const NUMBER_REQUEST_APPROVED_MESSAGE = "Your request was approved. Our team will contact you about the next steps.";
export const NUMBER_REQUEST_REJECTED_MESSAGE = "Your request was not approved.";
/** The shared client's own word for a 401 that carried no message (http-client.ts defaultMessageForStatus). */
const CLIENT_DEFAULT_401_MESSAGE = "Unauthorized";

/**
 * The 5xx codes the backend sends on purpose, with a message written for the customer:
 * telephony_number_requests.py (storage_unavailable, save_unconfirmed) and the shared
 * handler for a service that is down (error_handlers.py service_unavailable).
 */
const DELIBERATE_SERVER_CODES: ReadonlySet<string> = new Set(["storage_unavailable", "save_unconfirmed", "service_unavailable"]);

/** 60 s plus the time two files need at 25,000 bytes a second; never below 120 s. */
export function numberRequestTimeoutMs(totalBytes: number): number {
    return Math.max(120_000, 60_000 + Math.ceil((Math.max(0, totalBytes) / 25_000) * 1000));
}

/**
 * What to tell the customer when a submit fails. Anything that carries the
 * server's own envelope keeps the server's message. A timeout, a network
 * failure or a 5xx with no envelope (the client's own "server_error") may have
 * saved the documents, so the server's "could not confirm" sentence is used.
 * Anything else that is not an API error gets the server's "could not be saved" sentence.
 * A 401 keeps the server's message too; with none, the server's usual "session has ended" sentence is used.
 */
export function numberRequestFailureMessage(err: unknown): string {
    if (!isApiClientError(err)) return NUMBER_REQUEST_NOT_SAVED_MESSAGE;
    // The session ended and could not be renewed: the server's own sentence, or its usual one when it sent none.
    if (err.status === 401) return err.message && err.message !== CLIENT_DEFAULT_401_MESSAGE ? err.message : NUMBER_REQUEST_SESSION_ENDED_MESSAGE;
    // The browser says it is offline: the request never left, and the client's own message says so.
    if (err.code === "network_error" && typeof navigator !== "undefined" && navigator.onLine === false) {
        return err.message || NUMBER_REQUEST_UNCONFIRMED_MESSAGE;
    }
    if (err.code === "timeout" || err.code === "network_error") return NUMBER_REQUEST_UNCONFIRMED_MESSAGE;
    // Any 5xx may have saved the documents; only the backend's deliberate answers for this route keep their message.
    if ((err.status ?? 0) >= 500) return DELIBERATE_SERVER_CODES.has(err.code) ? err.message || NUMBER_REQUEST_NOT_SAVED_MESSAGE : NUMBER_REQUEST_UNCONFIRMED_MESSAGE;
    return err.message || NUMBER_REQUEST_NOT_SAVED_MESSAGE;
}

export async function getNumberRequestConfig(signal?: AbortSignal): Promise<NumberRequestConfig> {
    const data = await sharedHttpClient().request({ path: "/telephony/number-requests/config", method: "GET", signal });
    return configSchema.parse(data);
}

/**
 * Sends one request: `use_type` first, then exactly the two files of that use.
 * The same `idempotencyKey` with the same files is a safe retry on the server.
 */
export async function submitNumberRequest(input: {
    useType: NumberRequestUseType;
    files: Record<string, File>;
    idempotencyKey: string;
    signal?: AbortSignal;
}): Promise<NumberRequest | null> {
    const form = new FormData();
    form.append("use_type", input.useType);
    for (const field of NUMBER_REQUEST_FIELDS[input.useType]) {
        const chosen = input.files[field];
        if (!chosen) throw new Error(`Missing file for ${field}`);
        form.append(field, chosen, chosen.name);
    }
    const data = await sharedHttpClient().request({
        path: "/telephony/number-requests",
        method: "POST",
        body: form,
        headers: { "Idempotency-Key": input.idempotencyKey },
        timeoutMs: numberRequestTimeoutMs(Object.values(input.files).reduce((total, f) => total + f.size, 0)),
        // A session that ended must not send the customer to the login page: the window and the chosen files stay.
        suppressAuthRedirect: true,
        signal: input.signal,
    });
    // Any 2xx means the documents are saved: a body that is not the expected shape is not a failure.
    const parsed = requestSchema.safeParse(data);
    return parsed.success ? parsed.data : null;
}

/**
 * A refusal before upload, in the server's own sentences (errors.py empty_file,
 * file_too_large, unsupported_file_type), or null when the file may be sent.
 * Decided on the extension only: phones and cloud pickers report odd or empty
 * types, and a name with no extension is left for the server, which checks the content.
 */
export function refuseNumberRequestFile(file: File, field: string, config: NumberRequestConfig): string | null {
    const label = NUMBER_REQUEST_LABELS[field] ?? "This document";
    if (file.size === 0) return `${label} is empty.`;
    if (file.size > config.max_file_bytes) return `${label} is larger than ${Math.floor(config.max_file_bytes / 1048576)} MB.`;
    const dot = file.name.lastIndexOf(".");
    const extension = dot === -1 || dot === file.name.length - 1 ? "" : file.name.slice(dot).toLowerCase();
    if (extension !== "" && !config.accepted_extensions.includes(extension)) return `${label} must be a PDF, JPG or PNG file.`;
    return null;
}

/** True when the server refused the key because it was already used with other documents (400 idempotency_key_reused). */
export function isNumberRequestKeyReused(err: unknown): boolean {
    return isApiClientError(err) && err.code === "idempotency_key_reused";
}

/**
 * One-way SHA-256 (64 hex characters) of the two files' details: name, size, last-changed date and type.
 * This, never a file name, is what is kept beside a key. Null when the browser cannot hash; the key then stays in memory only.
 */
export async function hashNumberRequestFiles(files: readonly File[]): Promise<string | null> {
    try {
        const details = files.map((f) => [f.name, f.size, f.lastModified, f.type].join("|")).join("\n");
        const digest = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(details));
        return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
    } catch {
        return null;
    }
}

// The key of an unfinished request, kept for this browser tab so a retry after leaving the Telephony tab or a reload
// finds the first request. Same shape as the saved purchase (billing-purchase.ts readSavedPurchase / storeSavedPurchase):
// sessionStorage, one entry per signed-in person and account, every access inside try/catch.
const savedKeySchema = z.object({ key: z.string().regex(/^[A-Za-z0-9_-]{8,128}$/), files: z.string().regex(/^[0-9a-f]{64}$/) });
export type SavedNumberRequestKey = z.infer<typeof savedKeySchema>;
const keyStorageName = (scope: string, useType: NumberRequestUseType) => `talklee.number-request-key.v1:${encodeURIComponent(scope)}:${useType}`;

/** The kept key for this person, account and use, or null (nothing kept, unreadable, or storage closed). Never throws. */
export function readSavedNumberRequestKey(scope: string, useType: NumberRequestUseType): SavedNumberRequestKey | null {
    try {
        const raw = window.sessionStorage.getItem(keyStorageName(scope, useType));
        if (!raw || raw.length > 400) return null;
        const result = savedKeySchema.safeParse(JSON.parse(raw));
        return result.success ? result.data : null;
    } catch {
        return null;
    }
}

/** Keeps the key and the files' hash, or removes the entry when `saved` is null. False when storage is closed. Never throws. */
export function storeSavedNumberRequestKey(scope: string, useType: NumberRequestUseType, saved: SavedNumberRequestKey | null): boolean {
    try {
        if (saved) window.sessionStorage.setItem(keyStorageName(scope, useType), JSON.stringify({ key: saved.key, files: saved.files }));
        else window.sessionStorage.removeItem(keyStorageName(scope, useType));
        return true;
    } catch {
        return false;
    }
}
