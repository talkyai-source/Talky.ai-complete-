import { sharedHttpClient } from "@/lib/api";

// CSV Upload Response
export interface BulkImportResponse {
    total_rows: number;
    imported: number;
    failed: number;
    duplicates_skipped: number;
    errors: Array<{ row: number; error: string; phone?: string }>;
}

// Analytics Types
export interface CallSeriesItem {
    date: string;
    total_calls: number;
    answered: number;
    failed: number;
}

export interface CallAnalyticsResponse {
    series: CallSeriesItem[];
}

// Recording Types
export interface Recording {
    id: string;
    call_id: string;
    phone_number?: string;
    created_at: string;
    duration_seconds?: number;
}

export interface RecordingListResponse {
    items: Recording[];
    page: number;
    page_size: number;
    total: number;
}

// Campaign Transcripts (Script Card)
export interface TranscriptTurn {
    role: "user" | "assistant";
    content: string;
    timestamp: string;
}

export interface CampaignCallWithTranscript {
    call_id: string;
    to_number: string;
    started_at: string;
    duration_seconds: number | null;
    outcome: string | null;
    turns: TranscriptTurn[];
}

export interface CampaignCallsResponse {
    items: CampaignCallWithTranscript[];
    page: number;
    page_size: number;
    total: number;
}

// Extended API - Real backend integration.
//
// AH-Phase-B: shared HttpClient instance (see lib/api.ts → sharedHttpClient).
// The two binary sites here (uploadCSV multipart, fetchRecordingBlob audio)
// go through `client.requestRaw` — same cookie+bearer auth and
// refresh-on-401 retry as every JSON call, but it returns the raw Response
// so binary/multipart bodies aren't JSON-parsed. Earlier these used bare
// fetch() with no refresh, so a rotated `talky_at` cookie 401'd them and
// surfaced as "Failed to load audio" / failed upload.
class ExtendedApi {
    private get client() { return sharedHttpClient(); }

    // CSV Upload
    async uploadCSV(campaignId: string, file: File, skipDuplicates: boolean = true): Promise<BulkImportResponse> {
        const formData = new FormData();
        formData.append("file", file);

        const response = await this.client.requestRaw({
            path: `/contacts/campaigns/${campaignId}/upload`,
            method: "POST",
            query: { skip_duplicates: String(skipDuplicates) },
            body: formData,
        });

        return (await response.json()) as BulkImportResponse;
    }

    // Analytics
    async getCallAnalytics(
        fromDate?: string,
        toDate?: string,
        groupBy: "day" | "week" | "month" = "day"
    ): Promise<CallAnalyticsResponse> {
        const params: Record<string, string> = { group_by: groupBy };
        if (fromDate) params.from = fromDate;
        if (toDate) params.to = toDate;

        return this.client.request({
            path: "/analytics/calls",
            method: "GET",
            params,
        });
    }

    // Recordings
    async listRecordings(
        callId?: string,
        page: number = 1,
        pageSize: number = 20
    ): Promise<RecordingListResponse> {
        const params: Record<string, string> = {
            page: String(page),
            page_size: String(pageSize),
        };
        if (callId) params.call_id = callId;

        return this.client.request({
            path: "/recordings",
            method: "GET",
            params,
        });
    }

    async fetchRecordingBlob(recordingId: string): Promise<string> {
        // Route through requestRaw so this binary stream gets the SAME auth
        // (cookie + optional bearer) AND refresh-on-401 retry as every JSON
        // call. The previous bare fetch() did no refresh, so once the
        // short-lived talky_at cookie rotated (~15 min) it 401'd and showed
        // "Failed to load audio" even though the backend was healthy.
        const response = await this.client.requestRaw({
            path: `/recordings/${recordingId}/stream`,
            method: "GET",
        });
        const blob = await response.blob();
        return URL.createObjectURL(blob);
    }

    // Campaign call transcripts (Script Card)
    async getCampaignCallsWithTranscripts(
        campaignId: string,
        page: number = 1,
        pageSize: number = 20
    ): Promise<CampaignCallsResponse> {
        return this.client.request({
            path: `/campaigns/${campaignId}/calls`,
            method: "GET",
            params: {
                page: String(page),
                page_size: String(pageSize),
            },
        });
    }
}

export const extendedApi = new ExtendedApi();
