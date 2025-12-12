import { api, MeResponse, AuthResponse } from "./api";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

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

// Extended API Client
class DashboardApi {
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
        const response = await fetch(`${API_BASE}${endpoint}`, {
            ...options,
            headers: this.getHeaders(),
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: "Request failed" }));
            throw new Error(error.detail);
        }

        return response.json();
    }

    // Dashboard
    async getDashboardSummary(): Promise<DashboardSummary> {
        return this.request<DashboardSummary>("/dashboard/summary");
    }

    // Campaigns
    async listCampaigns(): Promise<{ campaigns: Campaign[] }> {
        return this.request<{ campaigns: Campaign[] }>("/campaigns/");
    }

    async getCampaign(id: string): Promise<{ campaign: Campaign }> {
        return this.request<{ campaign: Campaign }>(`/campaigns/${id}`);
    }

    async createCampaign(data: CampaignCreate): Promise<{ campaign: Campaign }> {
        return this.request<{ campaign: Campaign }>("/campaigns/", {
            method: "POST",
            body: JSON.stringify(data),
        });
    }

    async startCampaign(id: string): Promise<{ message: string; jobs_enqueued: number }> {
        return this.request<{ message: string; jobs_enqueued: number }>(`/campaigns/${id}/start`, {
            method: "POST",
        });
    }

    async pauseCampaign(id: string): Promise<{ message: string }> {
        return this.request<{ message: string }>(`/campaigns/${id}/pause`, {
            method: "POST",
        });
    }

    async stopCampaign(id: string): Promise<{ message: string }> {
        return this.request<{ message: string }>(`/campaigns/${id}/stop`, {
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
        return this.request(`/campaigns/${id}/stats`);
    }

    // Contacts
    async listContacts(
        campaignId: string,
        page: number = 1,
        pageSize: number = 50
    ): Promise<{ items: Contact[]; total: number; page: number; page_size: number }> {
        return this.request(`/campaigns/${campaignId}/contacts?page=${page}&page_size=${pageSize}`);
    }

    async addContact(
        campaignId: string,
        data: { phone_number: string; first_name?: string; last_name?: string; email?: string }
    ): Promise<{ message: string; contact: Contact }> {
        return this.request(`/campaigns/${campaignId}/contacts`, {
            method: "POST",
            body: JSON.stringify(data),
        });
    }

    // Calls
    async listCalls(page: number = 1, pageSize: number = 20): Promise<{ calls: Call[]; total: number }> {
        return this.request(`/calls?page=${page}&page_size=${pageSize}`);
    }

    async getCall(id: string): Promise<CallDetail> {
        return this.request(`/calls/${id}`);
    }

    async getCallTranscript(id: string, format: "json" | "text" = "json"): Promise<{
        format: string;
        turns?: Array<{ role: string; content: string; timestamp: string }>;
        transcript?: string;
        metadata?: Record<string, number>;
    }> {
        return this.request(`/calls/${id}/transcript?format=${format}`);
    }
}

export const dashboardApi = new DashboardApi();
