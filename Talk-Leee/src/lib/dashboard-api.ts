import { sharedHttpClient } from "@/lib/api";
import { apiBaseUrl } from "@/lib/env";

// Dashboard Types
export interface DashboardSummary {
    total_calls: number;
    answered_calls: number;
    failed_calls: number;
    minutes_used: number;
    // Plan allowance — Hisham's dashboard renders this in the usage gauge.
    // Optional for back-compat with older backends that don't return it.
    minutes_included?: number;
    minutes_remaining: number;
    active_campaigns: number;

    // Live + monthly aggregate fields exposed by /dashboard/summary.
    // Optional so the type stays compatible with older deployments;
    // page.tsx falls back to 0 when the field is missing.
    active_calls?: number;
    avg_call_duration_seconds?: number;
    queued_jobs?: number;
    outcome_breakdown?: Record<string, number>;
}

// Campaign Types
export interface Campaign {
    id: string;
    name: string;
    description?: string;
    status: string;
    system_prompt: string;
    voice_id: string;
    tts_provider?: string | null;   // per-campaign TTS engine; null = tenant global
    max_concurrent_calls: number;
    total_leads: number;
    calls_completed: number;
    calls_failed: number;
    created_at: string;
    started_at?: string;
    completed_at?: string;
    script_config?: {
        persona_type?: PersonaType;
        company_name?: string;
        agent_names?: string[];
        campaign_slots?: Record<string, unknown>;
        additional_instructions?: string;
    };
}

export type PersonaType = "lead_gen" | "customer_support" | "receptionist";

export interface CampaignCreate {
    name: string;
    description?: string;
    // Freeform extra instructions. Backend always keeps this below the
    // production guardrails/persona prompt; it is never the full prompt.
    system_prompt: string;
    voice_id: string;
    goal?: string;

    // Required production prompt path. New/edited campaigns must use one
    // of the backend personas; custom text is only additional instructions.
    persona_type: PersonaType;
    company_name: string;
    agent_names: string[];      // 1..3 names — rotated per call
    campaign_slots: Record<string, unknown>;
    // Knowledge-first campaign (vectorless-RAG wizard): content comes from the
    // uploaded knowledge base, so per-persona content slots are not required and
    // the persona prompt is a lean identity+tone shell. Default false.
    knowledge_driven?: boolean;
    // Per-campaign TTS provider (cartesia|google|deepgram|elevenlabs). Omit to
    // use the tenant global. The voice_id is validated against this provider.
    tts_provider?: string;
}

// Call Types
export interface Call {
    id: string;
    campaign_id: string;
    campaign_name?: string;
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
    /** One-line AI summary headline returned by the call list endpoint. */
    summary?: string;
}

// AI Call Summary Types
export interface CallSummaryObj {
    headline: string;
    outcome: string;
    what_happened: string;
    key_points: string[];
    objections: Array<{ objection: string; handled: string }>;
    commitments: string[];
    action_items: Array<{ item: string; owner: string }>;
    sentiment: string;
    next_step: string;
    notable_quotes: string[];
}

export interface CallSummaryEnvelope {
    available: boolean;
    summary: CallSummaryObj | null;
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
    campaign_name?: string;
}

// Dashboard API - Real backend integration.
//
// AH-Phase-B: shared HttpClient instance (see lib/api.ts → sharedHttpClient).
// One instance, one single-flight refresh state, no parallel
// /auth/refresh races between dashboard-api and api.ts.
class DashboardApi {
    private get client() { return sharedHttpClient(); }

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

    async updateCampaign(id: string, data: CampaignCreate): Promise<{ campaign: Campaign }> {
        const response = await this.client.request<{ campaign: Campaign | null }>({
            path: `/campaigns/${id}`,
            method: "PUT",
            body: data,
        });
        if (!response.campaign?.id) {
            throw new Error("Campaign update failed. The backend did not return an updated campaign.");
        }
        return { campaign: response.campaign };
    }

    /** Apply a TTS provider+voice to a chosen set of campaigns (per-campaign). */
    async applyTtsConfig(input: {
        tts_provider: string;
        tts_voice_id: string;
        campaign_ids: string[];
    }): Promise<{ updated: string[]; count: number }> {
        return this.client.request({
            path: "/campaigns/apply-tts-config",
            method: "POST",
            body: input,
        });
    }

    async previewCampaignPrompt(input: {
        persona_type: "lead_gen" | "customer_support" | "receptionist";
        company_name: string;
        agent_name: string;
        campaign_slots: Record<string, unknown>;
        additional_instructions?: string;
        direction?: "outbound" | "inbound";
        knowledge_driven?: boolean;
    }): Promise<{
        system_prompt: string;
        greeting: string;
        direction: "outbound" | "inbound";
        has_inbound_directive: boolean;
        prompt_chars: number;
    }> {
        return this.client.request({
            path: "/campaigns/preview-prompt",
            method: "POST",
            body: {
                persona_type: input.persona_type,
                company_name: input.company_name,
                agent_name: input.agent_name,
                campaign_slots: input.campaign_slots,
                additional_instructions: input.additional_instructions,
                direction: input.direction ?? "outbound",
                knowledge_driven: input.knowledge_driven ?? false,
            },
        });
    }

    async startCampaign(
        id: string,
        opts?: { first_speaker?: "agent" | "user" },
    ): Promise<{ message: string; jobs_enqueued: number }> {
        return this.client.request({
            path: `/campaigns/${id}/start`,
            method: "POST",
            body: { first_speaker: opts?.first_speaker ?? "agent" },
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
        const response = await this.client.request<{ items: (CallListItem & { summary?: string })[]; total: number }>({
            path: "/calls",
            method: "GET",
            params: { page: String(page), page_size: String(pageSize) },
        });

        // Map backend CallListItem to frontend Call format
        const calls: Call[] = response.items.map(item => ({
            id: item.id,
            campaign_id: "",
            campaign_name: item.campaign_name,
            lead_id: "",
            phone_number: item.to_number,
            status: item.status,
            outcome: item.outcome,
            duration_seconds: item.duration_seconds,
            created_at: item.timestamp,
            summary: item.summary,
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

    async getCallSummary(id: string): Promise<CallSummaryEnvelope> {
        return this.client.request<CallSummaryEnvelope>({
            path: `/calls/${id}/summary`,
            method: "GET",
            suppressAuthRedirect: true,
        });
    }
}

export const dashboardApi = new DashboardApi();
