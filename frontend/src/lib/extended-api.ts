import { dashboardApi, Campaign, Contact, Call, DashboardSummary } from "./dashboard-api";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

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

class ExtendedApi {
    private getHeaders(): HeadersInit {
        const headers: HeadersInit = {
            "Content-Type": "application/json",
        };
        if (typeof window !== "undefined") {
            const token = localStorage.getItem("token");
            if (token) {
                (headers as Record<string, string>)["Authorization"] = `Bearer ${token}`;
            }
        }
        return headers;
    }

    private async request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
        const headers = { ...this.getHeaders() };

        // Remove Content-Type for FormData (let browser set it with boundary)
        if (options.body instanceof FormData) {
            delete (headers as Record<string, string>)["Content-Type"];
        }

        const response = await fetch(`${API_BASE}${endpoint}`, {
            ...options,
            headers,
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: "Request failed" }));
            throw new Error(error.detail);
        }

        return response.json();
    }

    // CSV Upload
    async uploadCSV(campaignId: string, file: File, skipDuplicates: boolean = true): Promise<BulkImportResponse> {
        const formData = new FormData();
        formData.append("file", file);

        return this.request<BulkImportResponse>(
            `/contacts/campaigns/${campaignId}/upload?skip_duplicates=${skipDuplicates}`,
            {
                method: "POST",
                body: formData,
            }
        );
    }

    // Analytics
    async getCallAnalytics(
        fromDate?: string,
        toDate?: string,
        groupBy: "day" | "week" | "month" = "day"
    ): Promise<CallAnalyticsResponse> {
        const params = new URLSearchParams();
        if (fromDate) params.append("from", fromDate);
        if (toDate) params.append("to", toDate);
        params.append("group_by", groupBy);

        return this.request<CallAnalyticsResponse>(`/analytics/calls?${params.toString()}`);
    }

    // Recordings
    async listRecordings(
        callId?: string,
        page: number = 1,
        pageSize: number = 20
    ): Promise<RecordingListResponse> {
        const params = new URLSearchParams();
        if (callId) params.append("call_id", callId);
        params.append("page", page.toString());
        params.append("page_size", pageSize.toString());

        return this.request<RecordingListResponse>(`/recordings/?${params.toString()}`);
    }

    getRecordingStreamUrl(recordingId: string): string {
        return `${API_BASE}/recordings/${recordingId}/stream`;
    }
}

export const extendedApi = new ExtendedApi();
