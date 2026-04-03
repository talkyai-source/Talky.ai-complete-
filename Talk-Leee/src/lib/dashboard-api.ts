import { createHttpClient } from "@/lib/http-client";
import { apiBaseUrl } from "@/lib/env";

// Dashboard Types
export interface DashboardSummary {
    total_calls: number;
    answered_calls: number;
    failed_calls: number;
    minutes_used: number;
    minutes_remaining: number;
    active_campaigns: number;
}

// Campaign Types
export interface Campaign {
    id: string;
    name: string;
    description?: string;
    status: string;
    system_prompt: string;
    voice_id: string;
    max_concurrent_calls: number;
    total_leads: number;
    calls_completed: number;
    calls_failed: number;
    created_at: string;
    started_at?: string;
    completed_at?: string;
}

export interface CampaignCreate {
    name: string;
    description?: string;
    system_prompt: string;
    voice_id: string;
    goal?: string;
}

// Call Types
export interface Call {
    id: string;
    campaign_id: string;
    lead_id: string;
    phone_number: string;
    status: string;
    outcome?: string;
    duration_seconds?: number;
    transcript?: string;
    recording_url?: string;
    created_at: string;
    started_at?: string;
    ended_at?: string;
}

export interface CallDetail extends Call {
    summary?: string;
    recording_id?: string;
}

// Contact Types
export interface Contact {
    id: string;
    campaign_id: string;
    phone_number: string;
    first_name?: string;
    last_name?: string;
    email?: string;
    status: string;
    last_call_result: string;
    call_attempts: number;
    created_at: string;
}

// Internal types for backend responses
interface CallListItem {
    id: string;
    talklee_call_id?: string;
    timestamp: string;
    to_number: string;
    status: string;
    duration_seconds?: number;
    outcome?: string;
}

// Dashboard API - Real backend integration
class DashboardApi {
    private client = createHttpClient({ baseUrl: apiBaseUrl() });

    // Dashboard
    async getDashboardSummary(): Promise<DashboardSummary> {
        return this.client.request({
            path: "/dashboard/summary",
            method: "GET",
        });
    }

    // Campaigns
    async listCampaigns(): Promise<{ campaigns: Campaign[] }> {
        return this.client.request({
            path: "/campaigns",
            method: "GET",
        });
    }

    async getCampaign(id: string): Promise<{ campaign: Campaign }> {
        return this.client.request({
            path: `/campaigns/${id}`,
            method: "GET",
        });
    }

    async createCampaign(data: CampaignCreate): Promise<{ campaign: Campaign }> {
        const response = await this.client.request<{ campaign: Campaign | null }>({
            path: "/campaigns",
            method: "POST",
            body: data,
        });
        if (!response.campaign?.id) {
            throw new Error("Campaign creation failed. The backend did not return a created campaign.");
        }
        return { campaign: response.campaign };
    }

    async startCampaign(id: string): Promise<{ message: string; jobs_enqueued: number }> {
        return this.client.request({
            path: `/campaigns/${id}/start`,
            method: "POST",
        });
    }

    async pauseCampaign(id: string): Promise<{ message: string }> {
        return this.client.request({
            path: `/campaigns/${id}/pause`,
            method: "POST",
        });
    }

    async stopCampaign(id: string): Promise<{ message: string }> {
        return this.client.request({
            path: `/campaigns/${id}/stop`,
            method: "POST",
        });
    }

    async getCampaignStats(id: string): Promise<{
        campaign_id: string;
        campaign_status: string;
        total_leads: number;
        job_status_counts: Record<string, number>;
        call_outcome_counts: Record<string, number>;
        goals_achieved: number;
    }> {
        return this.client.request({
            path: `/campaigns/${id}/stats`,
            method: "GET",
        });
    }

    // Contacts
    async listContacts(
        campaignId: string,
        page: number = 1,
        pageSize: number = 50
    ): Promise<{ items: Contact[]; total: number; page: number; page_size: number }> {
        return this.client.request({
            path: `/campaigns/${campaignId}/contacts`,
            method: "GET",
            params: { page: String(page), page_size: String(pageSize) },
        });
    }

    async addContact(
        campaignId: string,
        data: { phone_number: string; first_name?: string; last_name?: string; email?: string }
    ): Promise<{ message: string; contact: Contact }> {
        return this.client.request({
            path: `/campaigns/${campaignId}/contacts`,
            method: "POST",
            body: data,
        });
    }

    // Calls
    async listCalls(page: number = 1, pageSize: number = 20): Promise<{ calls: Call[]; total: number }> {
        const response = await this.client.request<{ items: CallListItem[]; total: number }>({
            path: "/calls",
            method: "GET",
            params: { page: String(page), page_size: String(pageSize) },
        });
        
        // Map backend CallListItem to frontend Call format
        const calls: Call[] = response.items.map(item => ({
            id: item.id,
            campaign_id: "", // Not included in list response
            lead_id: "", // Not included in list response
            phone_number: item.to_number,
            status: item.status,
            outcome: item.outcome,
            duration_seconds: item.duration_seconds,
            created_at: item.timestamp,
        }));
        
        return {
            calls,
            total: response.total,
        };
    }

    async getCall(id: string): Promise<CallDetail> {
        const response = await this.client.request<{
            id: string;
            talklee_call_id?: string;
            timestamp: string;
            to_number: string;
            status: string;
            duration_seconds?: number;
            outcome?: string;
            transcript?: string;
            recording_id?: string;
            campaign_id?: string;
            lead_id?: string;
            summary?: string;
        }>({
            path: `/calls/${id}`,
            method: "GET",
        });
        
        // Map backend response to frontend format
        return {
            id: response.id,
            campaign_id: response.campaign_id || "",
            lead_id: response.lead_id || "",
            phone_number: response.to_number,
            status: response.status,
            outcome: response.outcome,
            duration_seconds: response.duration_seconds,
            transcript: response.transcript,
            recording_url: response.recording_id ? this.getRecordingUrl(response.recording_id) : undefined,
            created_at: response.timestamp,
            summary: response.summary,
            recording_id: response.recording_id,
        };
    }
    
    private getRecordingUrl(recordingId: string): string {
        return `${apiBaseUrl()}/recordings/${recordingId}/stream`;
    }

    async getCallTranscript(id: string, format: "json" | "text" = "json"): Promise<{
        format: string;
        turns?: Array<{ role: string; content: string; timestamp: string }>;
        transcript?: string;
        metadata?: Record<string, number>;
    }> {
        return this.client.request({
            path: `/calls/${id}/transcript`,
            method: "GET",
            params: { format },
        });
    }
}

export const dashboardApi = new DashboardApi();
