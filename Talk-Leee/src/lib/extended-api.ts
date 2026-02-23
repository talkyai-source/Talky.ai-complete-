import { createHttpClient } from "@/lib/http-client";
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
    created_at: string;
    duration_seconds?: number;
}

export interface RecordingListResponse {
    items: Recording[];
    page: number;
    page_size: number;
    total: number;
}

// Extended API - Real backend integration
class ExtendedApi {
    private client = createHttpClient({ baseUrl: apiBaseUrl() });

    // CSV Upload
    async uploadCSV(campaignId: string, file: File, skipDuplicates: boolean = true): Promise<BulkImportResponse> {
        const formData = new FormData();
        formData.append("file", file);

        // Get auth token from storage
        const token = this.client.getToken();
        const headers: Record<string, string> = {};
        if (token) {
            headers.Authorization = `Bearer ${token}`;
        }

        const response = await fetch(`${apiBaseUrl()}/contacts/campaigns/${campaignId}/upload?skip_duplicates=${skipDuplicates}`, {
            method: "POST",
            body: formData,
            headers,
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

    getRecordingStreamUrl(recordingId: string): string {
        return `${apiBaseUrl()}/recordings/${recordingId}/stream`;
    }
}

export const extendedApi = new ExtendedApi();
