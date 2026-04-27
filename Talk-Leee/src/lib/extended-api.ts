// PROTOTYPE MODE - All APIs return dummy data

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

// Dummy Analytics Data
const DUMMY_ANALYTICS: CallSeriesItem[] = [
    { date: "2024-12-24", total_calls: 145, answered: 128, failed: 17 },
    { date: "2024-12-25", total_calls: 52, answered: 45, failed: 7 },
    { date: "2024-12-26", total_calls: 189, answered: 165, failed: 24 },
    { date: "2024-12-27", total_calls: 210, answered: 184, failed: 26 },
    { date: "2024-12-28", total_calls: 178, answered: 156, failed: 22 },
    { date: "2024-12-29", total_calls: 234, answered: 205, failed: 29 },
    { date: "2024-12-30", total_calls: 239, answered: 206, failed: 33 },
];

// Dummy Recordings
const DUMMY_RECORDINGS: Recording[] = [
    { id: "rec-001", call_id: "call-001", created_at: "2024-12-30T10:19:10Z", duration_seconds: 245 },
    { id: "rec-002", call_id: "call-002", created_at: "2024-12-30T10:23:03Z", duration_seconds: 180 },
    { id: "rec-003", call_id: "call-004", created_at: "2024-12-30T09:50:22Z", duration_seconds: 320 },
    { id: "rec-004", call_id: "call-006", created_at: "2024-12-30T10:36:39Z", duration_seconds: 95 },
    { id: "rec-005", call_id: "call-008", created_at: "2024-12-30T10:46:03Z", duration_seconds: 60 },
];

class ExtendedApi {
    // CSV Upload
    async uploadCSV(_campaignId: string, _file: File, _skipDuplicates: boolean = true): Promise<BulkImportResponse> {
        void _campaignId;
        void _file;
        void _skipDuplicates;
        // Simulate successful upload
        return {
            total_rows: 150,
            imported: 142,
            failed: 3,
            duplicates_skipped: 5,
            errors: [
                { row: 23, error: "Invalid phone number format", phone: "invalid-phone" },
                { row: 67, error: "Missing required field", phone: "+1555000000" },
                { row: 98, error: "Invalid email format", phone: "+15551234567" },
            ],
        };
    }

    // Analytics
    async getCallAnalytics(
        _fromDate?: string,
        _toDate?: string,
        _groupBy: "day" | "week" | "month" = "day"
    ): Promise<CallAnalyticsResponse> {
        void _fromDate;
        void _toDate;
        void _groupBy;
        return { series: DUMMY_ANALYTICS };
    }

    // Recordings
    async listRecordings(
        _callId?: string,
        page: number = 1,
        pageSize: number = 20
    ): Promise<RecordingListResponse> {
        return {
            items: DUMMY_RECORDINGS,
            page,
            page_size: pageSize,
            total: DUMMY_RECORDINGS.length,
        };
    }

    getRecordingStreamUrl(recordingId: string): string {
        return `#recording-${recordingId}-prototype`;
    }
}

export const extendedApi = new ExtendedApi();
