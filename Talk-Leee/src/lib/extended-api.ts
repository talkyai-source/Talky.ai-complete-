import { sharedHttpClient } from "@/lib/api";
import { ApiClientError } from "@/lib/http-client";
import { FEEDBACK_MAX_SECONDS, feedbackFileName } from "@/lib/audio-recording";

/** A structured review of how the agent handled a call (goals.md §3). */
export interface ConversationReview {
    id: string;
    call_id: string;
    campaign_id: string | null;
    user_id: string;
    rating: number;
    tags: string[];
    comment: string | null;
    prompt_template: string | null;
    prompt_version: string | null;
    prompt_hash: string | null;
    /** Points granted by this submission. 0 on an edit — the reward is tied to
     *  the review once, by a database constraint. */
    awarded_points: number;
    created_at: string;
    updated_at: string;
}

/** Tag vocabulary and reward rules, served by the API so the UI never hardcodes them. */
export interface ReviewOptions {
    tags: string[];
    rewards_enabled: boolean;
    points_per_review: number;
    daily_cap: number;
    bare_rating_earns_reward: boolean;
}

/** One reviewer voice note about how the agent handled a call. */
export interface CallFeedback {
    id: string;
    call_id: string;
    audio_url: string;
    audio_mime_type: string;
    audio_size_bytes: number;
    duration_seconds: number | null;
    transcript: string | null;
    transcript_status: "pending" | "done" | "failed";
    transcript_error: string | null;
    transcription_attempts: number;
    retryable: boolean;
    created_at: string;
    updated_at: string;
}

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
    phone_number?: string | null;
    created_at: string;
    duration_seconds?: number | null;
    file_size_bytes?: number | null;
    status: string;
    legal_hold: boolean;
}

export interface RecordingListResponse {
    items: Recording[];
    page: number;
    page_size: number;
    total: number;
}

export interface DeleteRecordingInput {
    reason: string;
    idempotencyKey: string;
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
// Binary sites here (uploads and recording playback/download)
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

    // Paste-a-blob bulk import (Phase 3a). Same normalize/dedup/insert
    // pipeline as the CSV upload, but the input is free-form pasted text
    // (one number per line or comma/semicolon separated).
    async pasteContacts(campaignId: string, text: string): Promise<BulkImportResponse> {
        return this.client.request({
            path: `/contacts/campaigns/${campaignId}/paste`,
            method: "POST",
            body: { text },
        });
    }

    // Voice cloning (ElevenLabs Instant Voice Cloning).
    async listClonedVoices(): Promise<{
        items: Array<{ id: string; voice_id: string; name: string; created_at: string }>;
        max_per_tenant: number;
        used: number;
    }> {
        return this.client.request({ path: "/ai-options/voices/cloned", method: "GET" });
    }

    async cloneVoice(name: string, consent: boolean, file: File | Blob): Promise<{ id: string; voice_id: string; name: string }> {
        const form = new FormData();
        form.append("name", name);
        form.append("consent", consent ? "true" : "false");
        // Give a blob a filename so the backend's extension check passes.
        const named = file instanceof File ? file : new File([file], "sample.webm", { type: (file as Blob).type || "audio/webm" });
        form.append("file", named);
        const res = await this.client.requestRaw({
            path: "/ai-options/voices/clone",
            method: "POST",
            body: form,
        });
        if (!res.ok) {
            let msg = `Clone failed (${res.status})`;
            try { const j = await res.json(); if (j?.detail) msg = j.detail; } catch { /* ignore */ }
            throw new Error(msg);
        }
        return (await res.json()) as { id: string; voice_id: string; name: string };
    }

    async deleteClonedVoice(id: string): Promise<{ deleted: boolean }> {
        return this.client.request({ path: `/ai-options/voices/cloned/${id}`, method: "DELETE" });
    }

    // Dialer insights (Phase 3e) — best time to call + retry effectiveness.
    async getBestTimeToCall(tz?: string): Promise<{
        timezone: string;
        best_hour: number | null;
        hours: Array<{ hour: number; total: number; answered: number; answer_rate: number; goal_achieved: number; goal_rate: number }>;
    }> {
        const params: Record<string, string> = {};
        if (tz) params.tz = tz;
        return this.client.request({ path: "/analytics/best-time", method: "GET", params });
    }

    async getRetryEffectiveness(): Promise<{
        attempts: Array<{ attempt: number; total: number; answered: number; answer_rate: number; goal_achieved: number; goal_rate: number }>;
    }> {
        return this.client.request({ path: "/analytics/retry-effectiveness", method: "GET" });
    }

    // Analytics
    async getCallAnalytics(
        fromDate?: string,
        toDate?: string,
        groupBy: "hour" | "day" | "week" | "month" = "day"
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

    // Real per-campaign call series (powers the dashboard campaign-lines chart).
    async getCallAnalyticsByCampaign(
        fromDate?: string,
        toDate?: string,
        groupBy: "hour" | "day" | "week" | "month" = "day"
    ): Promise<{ campaigns: Array<{ campaign_id: string; name: string; series: Array<{ date: string; total_calls: number; answered: number; failed: number; goal_achieved?: number }> }> }> {
        const params: Record<string, string> = { group_by: groupBy };
        if (fromDate) params.from = fromDate;
        if (toDate) params.to = toDate;

        return this.client.request({
            path: "/analytics/calls/by-campaign",
            method: "GET",
            params,
        });
    }

    // Recent critical call issues (e.g. "TTS out of credits") for the banner.
    async getRecentCallIssues(): Promise<{
        items: Array<{
            id: string;
            title: string;
            description?: string | null;
            severity?: string | null;
            metadata?: Record<string, unknown> | null;
        }>;
    }> {
        return this.client.request({
            path: "/events",
            method: "GET",
            params: { category: "call", severity: "critical", limit: "8" },
        });
    }

    // Recordings
    async listRecordings(
        callId?: string,
        page: number = 1,
        pageSize: number = 20,
        signal?: AbortSignal,
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
            signal,
        });
    }

    /**
     * Fetch bytes authorized for in-browser playback. The caller owns any
     * object URL it creates from this blob and must revoke that URL.
     */
    async fetchRecordingPlaybackBlob(recordingId: string, signal?: AbortSignal): Promise<Blob> {
        // Route through requestRaw so this binary stream gets the SAME auth
        // (cookie + optional bearer) AND refresh-on-401 retry as every JSON
        // call. The previous bare fetch() did no refresh, so once the
        // short-lived talky_at cookie rotated (~15 min) it 401'd and showed
        // "Failed to load audio" even though the backend was healthy.
        const response = await this.client.requestRaw({
            path: `/recordings/${recordingId}/stream`,
            method: "GET",
            signal,
        });
        return response.blob();
    }

    /** Download is a separate, independently authorized and audited action. */
    async downloadRecordingBlob(recordingId: string, signal?: AbortSignal): Promise<Blob> {
        const response = await this.client.requestRaw({
            path: `/recordings/${recordingId}/download`,
            method: "GET",
            signal,
        });
        return response.blob();
    }

    /**
     * Permanently delete recording media. The idempotency key belongs to one
     * dialog attempt and must be retained unchanged for transport retries.
     */
    async deleteRecording(
        recordingId: string,
        input: DeleteRecordingInput,
        signal?: AbortSignal,
    ): Promise<void> {
        await this.client.request<void>({
            path: `/recordings/${recordingId}`,
            method: "DELETE",
            headers: { "Idempotency-Key": input.idempotencyKey },
            body: { reason: input.reason },
            signal,
        });
    }

    // ── Call feedback voice notes ─────────────────────────────────────────
    // One note per call. Recorded in the browser, stored durably, then
    // transcribed by Deepgram inside the same request.

    /**
     * The note on this call, or null when there isn't one yet.
     *
     * `requestRaw` throws ApiClientError on every non-2xx, so "this call has no
     * note" arrives as a thrown 404 and has to be turned back into an ordinary
     * empty result — otherwise the commonest state renders as an error.
     */
    async getCallFeedback(callId: string): Promise<CallFeedback | null> {
        try {
            const res = await this.client.requestRaw({
                path: `/calls/${callId}/feedback`,
                method: "GET",
            });
            return (await res.json()) as CallFeedback;
        } catch (err) {
            if (err instanceof ApiClientError && err.status === 404) return null;
            throw err;
        }
    }

    /**
     * Upload a recording. `replace` supersedes an existing note atomically;
     * without it the backend answers 409 rather than overwrite someone's work.
     *
     * A 2xx here means the audio is durable. It does NOT mean transcription
     * succeeded — read `transcript_status`, which may be "failed".
     */
    async submitCallFeedback(
        callId: string,
        audio: Blob,
        opts: { durationSeconds?: number; replace?: boolean } = {},
    ): Promise<CallFeedback> {
        const form = new FormData();
        // Send the container the browser actually produced, and give the blob a
        // filename whose extension agrees with it.
        const type = audio.type || "audio/webm";
        form.append("audio", new File([audio], feedbackFileName(type), { type }));
        if (typeof opts.durationSeconds === "number" && Number.isFinite(opts.durationSeconds)) {
            const clamped = Math.min(Math.max(opts.durationSeconds, 0), FEEDBACK_MAX_SECONDS);
            form.append("duration_seconds", clamped.toFixed(2));
        }
        if (opts.replace) form.append("replace", "true");

        const res = await this.client.requestRaw({
            path: `/calls/${callId}/feedback`,
            method: "POST",
            body: form,
        });
        return (await res.json()) as CallFeedback;
    }

    // ── Conversation reviews (goals.md §3) ────────────────────────────────
    // Distinct from the voice note above: a structured rating + tags + comment,
    // one per USER per call, so teammates can review the same call separately.

    /**
     * Every review in the tenant, filtered on the four axes goals.md §3 names:
     * campaign, prompt version, rating and tag. Admin-only.
     */
    async listReviews(filters: {
        campaign_id?: string;
        prompt_version?: string;
        rating_min?: number;
        rating_max?: number;
        tag?: string;
        page?: number;
        page_size?: number;
    } = {}): Promise<{
        items: ConversationReview[];
        total: number;
        page: number;
        page_size: number;
    }> {
        const query: Record<string, string> = {};
        for (const [k, v] of Object.entries(filters)) {
            if (v !== undefined && v !== null && v !== "") query[k] = String(v);
        }
        return this.client.request({ path: "/reviews", method: "GET", query });
    }

    /**
     * Aggregate by prompt version and failure category — the Safe Improvement
     * Loop's query. `low_rated` counts 1s and 2s: the calls to go and listen to.
     */
    async getReviewSummary(filters: { campaign_id?: string; prompt_version?: string } = {}): Promise<{
        totals: { reviews: number; avg_rating: number | null; low_rated: number; calls_reviewed: number };
        by_prompt_version: Array<{ prompt_version: string; reviews: number; avg_rating: number | null; low_rated: number }>;
        by_tag: Array<{ tag: string; reviews: number; avg_rating: number | null }>;
    }> {
        const query: Record<string, string> = {};
        for (const [k, v] of Object.entries(filters)) if (v) query[k] = String(v);
        return this.client.request({ path: "/reviews/summary", method: "GET", query });
    }

    /** Tag vocabulary and reward rules. Fetch once, render the form from it. */
    async getReviewOptions(): Promise<ReviewOptions> {
        return this.client.request({ path: "/calls/reviews/options", method: "GET" });
    }

    /**
     * This user's own review of the call, or null when they have not left one.
     * Like getCallFeedback, "none yet" arrives as a thrown 404 and has to be
     * turned back into an ordinary empty result.
     */
    async getMyReview(callId: string): Promise<ConversationReview | null> {
        try {
            return await this.client.request({
                path: `/calls/${callId}/review`, method: "GET",
            });
        } catch (err) {
            if (err instanceof ApiClientError && err.status === 404) return null;
            throw err;
        }
    }

    /** Every review on this call — several teammates may have rated it. */
    async listCallReviews(callId: string): Promise<ConversationReview[]> {
        return this.client.request({ path: `/calls/${callId}/reviews`, method: "GET" });
    }

    /**
     * Leave or update this user's review. PUT, not POST: a user has at most one
     * review per call, so submitting again edits the same row rather than
     * creating a second.
     */
    async submitReview(
        callId: string,
        review: { rating: number; tags: string[]; comment?: string | null },
    ): Promise<ConversationReview> {
        return this.client.request({
            path: `/calls/${callId}/review`,
            method: "PUT",
            body: { rating: review.rating, tags: review.tags, comment: review.comment ?? null },
        });
    }

    /** Re-run transcription against the already-stored audio. */
    async retryCallFeedbackTranscription(callId: string): Promise<CallFeedback> {
        const res = await this.client.requestRaw({
            path: `/calls/${callId}/feedback/transcription/retry`,
            method: "POST",
        });
        return (await res.json()) as CallFeedback;
    }

    /**
     * Same reasoning as recording playback: route the binary through requestRaw
     * so it carries auth and inherits refresh-on-401. In production this 302s
     * to a short-lived S3 URL, which fetch follows on our behalf.
     */
    async fetchCallFeedbackAudioBlob(callId: string): Promise<string> {
        const res = await this.client.requestRaw({
            path: `/calls/${callId}/feedback/audio`,
            method: "GET",
        });
        return URL.createObjectURL(await res.blob());
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
