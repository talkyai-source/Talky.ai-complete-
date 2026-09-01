/**
 * Contact fields and captured lead details (goals.md §11 + §7).
 *
 * A dedicated module rather than more methods on extended-api: these all hang
 * off one backend concept, and the field SPEC is fetched rather than hardcoded
 * so the form, the table and the import mapper cannot drift from what the
 * server actually accepts.
 */
import { sharedHttpClient } from "@/lib/api";

/** Where a captured value came from, least to most trusted. */
export type CaptureSource =
    | "agent_inferred"
    | "imported"
    | "caller_stated"
    | "manual_edit";

export const SOURCE_LABEL: Record<CaptureSource, string> = {
    agent_inferred: "Inferred by the agent",
    imported: "From the imported list",
    caller_stated: "Said by the caller",
    manual_edit: "Edited by a person",
};

/**
 * §7: "Do not treat inferred values as confirmed facts." An inference is shown
 * amber and unconfirmed; a stated-and-confirmed value is shown plainly. The
 * distinction has to survive into the UI or it was pointless to record.
 */
export const SOURCE_TONE: Record<CaptureSource, string> = {
    agent_inferred:
        "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400",
    imported: "border-border bg-muted text-muted-foreground",
    caller_stated:
        "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
    manual_edit:
        "border-blue-500/40 bg-blue-500/10 text-blue-700 dark:text-blue-400",
};

export interface ContactFieldSpec {
    key: string;
    label: string;
    field_type: string;
    aliases: string[];
    agent_usable: boolean;
    max_len: number;
}

export interface CapturedDetail {
    field_key: string;
    field_type: string;
    value: string | null;
    source: CaptureSource;
    confirmed: boolean;
    is_required: boolean;
    updated_at: string;
}

export interface CampaignLeadField {
    field_key: string;
    label: string;
    field_type: string;
    is_required: boolean;
    agent_visible: boolean;
    user_visible: boolean;
    options?: string[] | null;
    sort_order: number;
}

export interface ImportIssue {
    row: number;
    field: string;
    value: string;
    reason: string;
}

export interface ImportPreview {
    headers: Record<string, string | null>;
    unmapped: string[];
    total_rows: number;
    valid_rows: number;
    issues: ImportIssue[];
    duplicates_in_file: { row: number; first_seen_row: number; key: string }[];
    sample: Record<string, string>[];
}

export const leadDetailsApi = {
    async fieldSpec(): Promise<{
        fields: ContactFieldSpec[];
        csv_template_headers: string[];
    }> {
        return sharedHttpClient().request({ path: "/contacts/fields", method: "GET" });
    },

    /** Parse a CSV and report what WOULD happen. Writes nothing. */
    async previewImport(file: File): Promise<ImportPreview> {
        const form = new FormData();
        form.append("file", file);
        const res = await sharedHttpClient().requestRaw({
            path: "/contacts/import/preview",
            method: "POST",
            body: form,
        });
        return (await res.json()) as ImportPreview;
    },

    async detailsForCall(
        callId: string,
        campaignId?: string,
    ): Promise<{ details: CapturedDetail[]; missing_required: string[] }> {
        return sharedHttpClient().request({
            path: `/calls/${callId}/lead-details`,
            method: "GET",
            query: campaignId ? { campaign_id: campaignId } : undefined,
        });
    },

    async campaignFields(campaignId: string): Promise<{ fields: CampaignLeadField[] }> {
        return sharedHttpClient().request({
            path: `/campaigns/${encodeURIComponent(campaignId)}/lead-fields`,
            method: "GET",
        });
    },

    async setCampaignFields(
        campaignId: string,
        fields: CampaignLeadField[],
    ): Promise<{ fields: CampaignLeadField[] }> {
        return sharedHttpClient().request({
            path: `/campaigns/${encodeURIComponent(campaignId)}/lead-fields`,
            method: "PUT",
            body: fields,
        });
    },

    /**
     * A human correcting a value. Always lands as source=manual_edit, which
     * outranks everything, so it can never be overwritten by a later inference.
     */
    async correct(
        callId: string,
        fieldKey: string,
        value: string | null,
        fieldType = "text",
    ): Promise<{ ok: boolean }> {
        return sharedHttpClient().request({
            path: `/calls/${callId}/lead-details/${encodeURIComponent(fieldKey)}`,
            method: "PUT",
            body: { value, field_type: fieldType, confirmed: true },
        });
    },
};

/** A csv the import mapper can hand back to the user, using OUR column names. */
export function buildCsvTemplate(headers: string[]): string {
    return headers.join(",") + "\n";
}
