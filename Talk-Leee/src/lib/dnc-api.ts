import { z } from "zod";
import { sharedHttpClient } from "@/lib/api";

// QA F1-15 (QA-UI-151): the Do-Not-Call API (backend/app/api/v1/endpoints/dnc.py)
// had no screen. Reading is open to the tenant; adding, importing and removing
// are admin-only on the server (require_admin) and every change is audited there.

const dncEntrySchema = z.object({
    id: z.string(),
    tenant_id: z.string().nullable().optional(),
    normalized_number: z.string(),
    source: z.string(),
    reason: z.string().nullable().optional(),
    expires_at: z.string().nullable().optional(),
    created_at: z.string().nullable().optional(),
});
export type DncEntry = z.infer<typeof dncEntrySchema>;

const dncBulkResultSchema = z.object({
    accepted_count: z.number(),
    skipped_count: z.number(),
    invalid_count: z.number(),
    accepted: z.array(z.string()),
    invalid: z.array(z.string()),
});
export type DncBulkResult = z.infer<typeof dncBulkResultSchema>;

/** The server returns at most this many active entries, newest first. */
export const DNC_LIST_LIMIT = 200;

export const dncApi = {
    async list(): Promise<DncEntry[]> {
        const data = await sharedHttpClient().request({ path: "/dnc/", method: "GET", params: { limit: String(DNC_LIST_LIMIT) } });
        return z.array(dncEntrySchema).parse(data);
    },
    async add(input: { e164: string; defaultCountry?: string; reason?: string }): Promise<DncEntry> {
        const data = await sharedHttpClient().request({
            path: "/dnc/",
            method: "POST",
            body: { e164: input.e164, default_country: input.defaultCountry || undefined, reason: input.reason || undefined },
        });
        return dncEntrySchema.parse(data);
    },
    async bulkImport(input: { numbers: string[]; defaultCountry?: string; reason?: string }): Promise<DncBulkResult> {
        const data = await sharedHttpClient().request({
            path: "/dnc/bulk-import",
            method: "POST",
            body: { numbers: input.numbers, default_country: input.defaultCountry || undefined, reason: input.reason || undefined },
        });
        return dncBulkResultSchema.parse(data);
    },
    async remove(id: string): Promise<void> {
        await sharedHttpClient().request({ path: `/dnc/${encodeURIComponent(id)}`, method: "DELETE" });
    },
};
