import { sharedHttpClient } from "@/lib/api";
import { apiBaseUrl } from "@/lib/env";

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
// Note: the two bare-fetch sites in this file (uploadCSV multipart and
// fetchRecordingBlob binary) still need the raw fetch because the
// shared client assumes JSON bodies + responses. They consume
// `this.client.getToken()` for auth — which now returns the token from
// the shared client's tokenProvider, so they participate in the same
// rotation behaviour.
class ExtendedApi {
    private get client() { return sharedHttpClient(); }

    // CSV Upload
    async uploadCSV(campaignId: string, file: File, skipDuplicates: boolean = true): Promise<BulkImportResponse> {
        const formData = new FormData();
        formData.append("file", file);

        // Authenticate via the session cookie (credentials: "include"), not a
        // manual Authorization header — a stale localStorage bearer would
        // override the cookie and 401 on the strict session-bound bearer path.
        const response = await fetch(`${apiBaseUrl()}/contacts/campaigns/${campaignId}/upload?skip_duplicates=${skipDuplicates}`, {
            method: "POST",
            body: formData,
            credentials: "include",
        });

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(errorData.detail || `Upload failed: ${response.statusText}`);
        }

        return response.json();
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
        // Authenticate via the session cookie (credentials: "include"), NOT a
        // manual Authorization header. A stale localStorage bearer would
        // OVERRIDE the cookie and force the backend's strict session-bound
        // bearer path → 401 "Session-bound token required" ("Failed to load
        // audio"). The cookie path (talky_at) accepts the session directly.
        const response = await fetch(`${apiBaseUrl()}/recordings/${recordingId}/stream`, {
            credentials: "include",
        });

        if (!response.ok) {
            throw new Error(`Failed to fetch recording: ${response.statusText}`);
        }

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
