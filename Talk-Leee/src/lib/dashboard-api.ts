import { sharedHttpClient } from "@/lib/api";

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

// Tenant monthly call-minute quota — drives the remaining-minutes display
// and the disabled state of the Start button. `unlimited` (allocated 0)
// is the field to branch on; remaining_minutes is 0 in that case.
export interface MinutesStatus {
    allocated: number;
    used_minutes: number;
    remaining_minutes: number;
    unlimited: boolean;
    exhausted: boolean;
}

// Campaign Types
export interface Campaign {
    id: string;
    name: string;
    description?: string;
    status: string;
    /** Explicit call direction; absent on historical outbound campaigns. */
    direction?: "inbound" | "outbound";
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
        agent_name_genders?: Record<string, string>;
        campaign_slots?: Record<string, unknown>;
        additional_instructions?: string;
    };
    // Per-campaign calling hours + timezone (Phase 3c-v2). Null/absent = tenant default.
    calling_config?: CampaignCallingSchedule | null;
}

export type PersonaType = "lead_gen" | "customer_support" | "receptionist";

/** Per-campaign calling hours + timezone. All fields optional; the dialer
 *  overlays whatever is set onto the tenant default. `ignore_schedule` is the
 *  client's "call anytime" override — the UI still warns out-of-hours. */
export interface CampaignCallingSchedule {
    timezone?: string | null;
    time_window_start?: string | null;   // "HH:MM"
    time_window_end?: string | null;     // "HH:MM"
    allowed_days?: number[] | null;      // 0=Mon … 6=Sun
    ignore_schedule?: boolean;
}

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
    // Optional per-name gender ("male"|"female") so each call picks a name
    // matching the selected voice's gender.
    agent_name_genders?: Record<string, string>;
    campaign_slots: Record<string, unknown>;
    // Knowledge-first campaign (vectorless-RAG wizard): content comes from the
    // uploaded knowledge base, so per-persona content slots are not required and
    // the persona prompt is a lean identity+tone shell. Default false.
    knowledge_driven?: boolean;
    // Per-campaign TTS provider (cartesia|google|deepgram|elevenlabs). Omit to
    // use the tenant global. The voice_id is validated against this provider.
    tts_provider?: string;
    // Per-campaign calling hours + timezone (Phase 3c-v2).
    calling_schedule?: CampaignCallingSchedule;
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
    created_at: string;
    started_at?: string;
    ended_at?: string;
    /** One-line AI summary headline returned by the call list endpoint. */
    summary?: string;
    /** Recording id (if a recording exists) — enables inline play in the list. */
    recording_id?: string | null;
    /** AI per-call verdict ("qualified | …", "callback | …", "no_interest | …") — the "was this call a success" answer. */
    lead_outcome?: string | null;
    /** True when a reviewer has left a voice note on this call. */
    has_feedback?: boolean;
    /** Direction defaults to outbound for historical rows. */
    direction?: "inbound" | "outbound";
    /** Originating party. Null is valid for privacy-blocked inbound ANI. */
    from_number?: string | null;
    /** Destination party; for inbound calls this is the called public DID. */
    to_number?: string | null;
    /** Inbound caller identity. May be privacy-masked by the server. */
    caller_ani?: string | null;
    /** Public DID that received the inbound call. */
    called_did?: string | null;
    inbound_campaign_id?: string | null;
    assignment_id?: string | null;
    route_id?: string | null;
    route_version?: number | null;
    config_version?: number | null;
    config_checksum?: string | null;
    admission_status?: string | null;
    admission_reason?: string | null;
    consent_status?: string | null;
    processing_status?: string | null;
    billing_status?: string | null;
    billing_hold_reason?: string | null;
    recording_status?: string | null;
    transcript_status?: string | null;
    media_state?: string | null;
}

export interface CampaignTerminationSummary {
    status: "confirmed" | "partial" | "lookup_failed";
    total_selected: number;
    requested: number;
    attempted: number;
    confirmed: number;
    deferred: number;
    unconfirmed: number;
    missing_identity: number;
    reasons: Record<string, number>;
    lookup_error: string | null;
}

export interface CampaignControlResponse {
    message: string;
    campaign: Campaign & { termination_summary?: CampaignTerminationSummary };
    termination_summary: CampaignTerminationSummary;
}

export interface CallListFilters {
    direction?: "inbound" | "outbound";
    inboundCampaignId?: string;
}

// AI Call Summary Types
export interface CallSummaryObj {
    headline: string;
    outcome: string;
    /** Structured lead qualification extracted after the call; optional on historical summaries. */
    qualification_status?: "qualified" | "nurture" | "unqualified" | "unknown" | string;
    decision_maker_status?: string;
    identified_need?: string;
    timeline?: string;
    budget_information?: string;
    what_happened: string;
    key_points: string[];
    objections: Array<{ objection: string; handled: string }>;
    commitments: string[];
    action_items: Array<{ item: string; owner: string }>;
    sentiment: string;
    next_step: string;
    /** Actionable follow-up suggestions from the post-call AI (timing, what to send, which concern to lead with). */
    follow_up_tips?: string[];
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
    /** Additional mobile value; falls back to phone_number in API responses. */
    mobile_number?: string;
    business_number?: string;
    first_name?: string;
    last_name?: string;
    full_name?: string;
    email?: string;
    company_name?: string;
    job_title?: string;
    best_time_to_call?: string;
    timezone?: string;
    calling_notes?: string;
    preferred_contact_method?: string;
    do_not_call?: boolean;
    custom_fields?: Record<string, unknown>;
    status: string;
    last_call_result: string;
    call_attempts: number;
    created_at: string;
    // Lead qualification fields set by the post-call AI. Optional because they
    // only populate once a call flags this contact as a lead (the contacts
    // endpoint SELECT *s the leads row, so they arrive at runtime).
    is_lead?: boolean;
    follow_up_note?: string | null;
    qualified_at?: string | null;
    qualified_call_id?: string | null;
}

export interface ContactMutation {
    phone_number: string;
    mobile_number?: string;
    business_number?: string;
    first_name?: string;
    last_name?: string;
    full_name?: string;
    email?: string;
    company_name?: string;
    job_title?: string;
    best_time_to_call?: string;
    timezone?: string;
    calling_notes?: string;
    preferred_contact_method?: string;
    do_not_call?: boolean;
}

// Contact List Types (grouped uploads / pasted batches).
// A campaign's contacts are grouped into named lists — one per CSV upload or
// paste batch. A synthetic `{id:"ungrouped"}` list is returned by the backend
// whenever NULL-list leads exist; it CANNOT be toggled or called (the UI hides
// those controls for it) and its `created_at` is null.
export interface ContactList {
    id: string;
    name: string;
    /** Where the list came from ("csv", "paste", …). Absent for the synthetic ungrouped list. */
    source?: string | null;
    is_active: boolean;
    contact_count: number;
    created_at: string | null;
}

/** Result of POST /contact-lists/{id}/call — placing REAL outbound calls now. */
export interface ContactListCallResult {
    list_id: string;
    is_active: boolean;
    eligible_count: number;
    jobs_enqueued: number;
    started: boolean;
    message: string;
}

// Internal types for backend responses
interface CallListItem {
    id: string;
    talklee_call_id?: string;
    timestamp: string;
    to_number: string;
    from_number?: string | null;
    status: string;
    duration_seconds?: number;
    outcome?: string;
    campaign_name?: string;
    recording_id?: string | null;
    lead_outcome?: string | null;
    has_feedback?: boolean;
    campaign_id?: string | null;
    direction?: "inbound" | "outbound" | string;
    caller_ani?: string | null;
    called_did?: string | null;
    inbound_campaign_id?: string | null;
    assignment_id?: string | null;
    route_id?: string | null;
    route_version?: number | null;
    config_version?: number | null;
    config_checksum?: string | null;
    admission_status?: string | null;
    admission_reason?: string | null;
    consent_status?: string | null;
    processing_status?: string | null;
    billing_status?: string | null;
    billing_hold_reason?: string | null;
    recording_status?: string | null;
    transcript_status?: string | null;
    media_state?: string | null;
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
        /** Who talks first. Independent of direction. */
        opening_mode?: "agent_first" | "callee_first";
        knowledge_driven?: boolean;
    }): Promise<{
        system_prompt: string;
        greeting: string;
        direction: "outbound" | "inbound";
        opening_mode: "agent_first" | "callee_first";
        has_inbound_directive: boolean;
        prompt_chars: number;
        campaign_guidance_chars: number;
        campaign_guidance_budget_chars: number;
        /** Saving will be refused; the preview shows what a live call would run. */
        over_budget: boolean;
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
                opening_mode: input.opening_mode,
                knowledge_driven: input.knowledge_driven ?? false,
            },
        });
    }

    async startCampaign(
        id: string,
        opts?: { first_speaker?: "agent" | "user"; batch_size?: number; call_gap_seconds?: number },
    ): Promise<{ message: string; jobs_enqueued: number }> {
        const body: Record<string, unknown> = {
            first_speaker: opts?.first_speaker ?? "agent",
        };
        // Only send pacing settings when the caller set them, so omitting one
        // leaves the campaign's existing setting untouched.
        if (opts?.batch_size !== undefined && opts?.batch_size !== null) {
            body.batch_size = opts.batch_size;
        }
        if (opts?.call_gap_seconds !== undefined && opts?.call_gap_seconds !== null) {
            body.call_gap_seconds = opts.call_gap_seconds;
        }
        return this.client.request({
            path: `/campaigns/${id}/start`,
            method: "POST",
            body,
        });
    }

    async getMinutesStatus(): Promise<MinutesStatus> {
        return this.client.request({
            path: `/campaigns/minutes/status`,
            method: "GET",
        });
    }

    async pauseCampaign(id: string): Promise<CampaignControlResponse> {
        return this.client.request({
            path: `/campaigns/${id}/pause`,
            method: "POST",
        });
    }

    async stopCampaign(id: string): Promise<CampaignControlResponse> {
        return this.client.request({
            path: `/campaigns/${id}/stop`,
            method: "POST",
        });
    }

    async deleteCampaign(id: string): Promise<{ message: string }> {
        return this.client.request({
            path: `/campaigns/${id}`,
            method: "DELETE",
        });
    }

    async getCampaignStats(id: string): Promise<{
        campaign_id: string;
        campaign_status: string;
        total_leads: number;
        qualified_leads?: number;
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
        pageSize: number = 50,
        opts?: { listId?: string | null; search?: string | null }
    ): Promise<{ items: Contact[]; total: number; page: number; page_size: number }> {
        const params: Record<string, string> = { page: String(page), page_size: String(pageSize) };
        // `list_id` accepts a real list id OR the synthetic "ungrouped" sentinel.
        if (opts?.listId) params.list_id = opts.listId;
        const search = opts?.search?.trim();
        if (search) params.search = search;
        return this.client.request({
            path: `/campaigns/${campaignId}/contacts`,
            method: "GET",
            params,
        });
    }

    // Contact lists (grouped uploads). Fail-soft in the UI: callers show an
    // error strip on reject but never blank the page.
    async listContactLists(campaignId: string): Promise<ContactList[]> {
        return this.client.request({
            path: `/campaigns/${campaignId}/contact-lists`,
            method: "GET",
        });
    }

    /** Toggle a list active/inactive. The synthetic "ungrouped" list rejects this. */
    async updateContactList(listId: string, isActive: boolean): Promise<ContactList> {
        return this.client.request({
            path: `/contact-lists/${listId}`,
            method: "PATCH",
            body: { is_active: isActive },
        });
    }

    /** Places REAL outbound calls to a list's eligible contacts immediately.
     *  Treat like Start Campaign — confirm() before calling. */
    async callContactList(listId: string): Promise<ContactListCallResult> {
        return this.client.request({
            path: `/contact-lists/${listId}/call`,
            method: "POST",
        });
    }

    async addContact(
        campaignId: string,
        data: ContactMutation,
    ): Promise<{ message: string; contact: Contact }> {
        return this.client.request({
            path: `/campaigns/${campaignId}/contacts`,
            method: "POST",
            body: data,
        });
    }

    async deleteContact(
        campaignId: string,
        contactId: string
    ): Promise<{ message: string }> {
        // Soft-deletes the lead (status='deleted') on the backend. Works for
        // contacts added via the Add Contact button OR via CSV import — they
        // are all rows in the same leads table.
        return this.client.request({
            path: `/campaigns/${campaignId}/contacts/${contactId}`,
            method: "DELETE",
        });
    }

    async updateContact(
        campaignId: string,
        contactId: string,
        data: Partial<ContactMutation>
    ): Promise<{ message: string; contact: Contact }> {
        return this.client.request({
            path: `/campaigns/${campaignId}/contacts/${contactId}`,
            method: "PATCH",
            body: data,
        });
    }

    // Calls
    async listCalls(page: number = 1, pageSize: number = 20, filters: CallListFilters = {}): Promise<{ calls: Call[]; total: number }> {
        const response = await this.client.request<{ items: (CallListItem & { summary?: string })[]; total: number }>({
            path: "/calls",
            method: "GET",
            params: {
                page: String(page),
                page_size: String(pageSize),
                direction: filters.direction,
                inbound_campaign_id: filters.inboundCampaignId,
            },
        });

        // Map backend CallListItem to frontend Call format
        const calls: Call[] = response.items.map(item => ({
            id: item.id,
            campaign_id: item.campaign_id ?? "",
            campaign_name: item.campaign_name,
            lead_id: "",
            phone_number: item.direction === "inbound" ? item.from_number || item.caller_ani || "Private caller" : item.to_number,
            status: item.status,
            outcome: item.outcome,
            duration_seconds: item.duration_seconds,
            created_at: item.timestamp,
            summary: item.summary,
            recording_id: item.recording_id,
            lead_outcome: item.lead_outcome,
            has_feedback: item.has_feedback,
            direction: item.direction === "inbound" ? "inbound" : "outbound",
            from_number: item.from_number ?? item.caller_ani,
            to_number: item.to_number ?? item.called_did,
            caller_ani: item.caller_ani ?? item.from_number,
            called_did: item.called_did ?? item.to_number,
            inbound_campaign_id: item.inbound_campaign_id,
            assignment_id: item.assignment_id,
            route_id: item.route_id,
            route_version: item.route_version,
            config_version: item.config_version,
            config_checksum: item.config_checksum,
            admission_status: item.admission_status,
            admission_reason: item.admission_reason,
            consent_status: item.consent_status,
            processing_status: item.processing_status,
            billing_status: item.billing_status,
            billing_hold_reason: item.billing_hold_reason,
            recording_status: item.recording_status,
            transcript_status: item.transcript_status,
            media_state: item.media_state,
        }));
        
        return {
            calls,
            total: response.total,
        };
    }

    async getCall(id: string): Promise<CallDetail> {
        const response = await this.client.request<CallListItem & {
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
            phone_number: response.direction === "inbound" ? response.from_number || response.caller_ani || "Private caller" : response.to_number,
            status: response.status,
            outcome: response.outcome,
            duration_seconds: response.duration_seconds,
            transcript: response.transcript,
            created_at: response.timestamp,
            summary: response.summary,
            recording_id: response.recording_id,
            direction: response.direction === "inbound" ? "inbound" : "outbound",
            from_number: response.from_number ?? response.caller_ani,
            to_number: response.to_number ?? response.called_did,
            caller_ani: response.caller_ani ?? response.from_number,
            called_did: response.called_did ?? response.to_number,
            inbound_campaign_id: response.inbound_campaign_id,
            assignment_id: response.assignment_id,
            route_id: response.route_id,
            route_version: response.route_version,
            config_version: response.config_version,
            config_checksum: response.config_checksum,
            admission_status: response.admission_status,
            admission_reason: response.admission_reason,
            consent_status: response.consent_status,
            processing_status: response.processing_status,
            billing_status: response.billing_status,
            billing_hold_reason: response.billing_hold_reason,
            recording_status: response.recording_status,
            transcript_status: response.transcript_status,
            media_state: response.media_state,
        };
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
