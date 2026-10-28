/**
 * API Client for Admin Panel
 * Aligned with Talky.ai Backend API
 */

import type { HoldTarget, HoldRequest, HoldReceipt, FormalHoldStatus, HoldPropagation } from './formal-holds';

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1';

// Types - Define before usage
// Backend roles (RBAC): platform_admin > partner_admin > tenant_admin > user > readonly.
// 'admin' | 'super_admin' kept only for legacy/dummy-auth compatibility.
export type BackendRole =
    | 'platform_admin'
    | 'partner_admin'
    | 'tenant_admin'
    | 'user'
    | 'readonly'
    | 'admin'
    | 'super_admin';

export interface AdminUser {
    id: string;
    email: string;
    name: string;
    role: BackendRole | string;
    tenant_id?: string;
}

// Flat shape actually returned by POST /auth/login on the live backend.
export interface LoginResponse {
    access_token: string;
    token_type: string;
    user_id: string;
    tenant_id?: string | null;
    email: string;
    role: string;
    name?: string | null;
    business_name?: string | null;
    minutes_remaining: number;
    message: string;
    mfa_required?: boolean;
    mfa_challenge_token?: string;
}

// Flat shape returned by GET /auth/me.
export interface MeResponse {
    id: string;
    email: string;
    name: string | null;
    role: string;
    tenant_id?: string | null;
    business_name?: string | null;
    minutes_remaining?: number;
}

export interface AuthResponse {
    access_token: string;
    token_type: string;
    user_id: string;
    email: string;
    role: string;
    business_name?: string;
    minutes_remaining: number;
    message: string;
}

export interface TenantQueryParams {
    page?: string;
    limit?: string;
    search?: string;
    status?: string;
}

export interface TenantsResponse {
    tenants: TenantListItem[];
    pagination: Pagination;
}

export interface TenantListItem {
    id: string;
    business_name: string;
    plan_id: string | null;
    plan_name: string | null;
    minutes_used: number;
    minutes_allocated: number;
    status: string;  // 'active' | 'suspended' | 'inactive'
    user_count: number;
    campaign_count: number;
    max_concurrent_calls: number;
    created_at: string | null;
    updated_at?: string | null;
}

export interface QuotaUpdateRequest {
    expected_updated_at: string | null;
    minutes_allocated?: number;
    max_concurrent_calls?: number;
}

export interface TenantMutationReceipt {
    id: string;
    detail: string;
    changed: boolean;
    status: string;
    updated_at: string | null;
    minutes_allocated: number;
    max_concurrent_calls: number;
    audit_event_id: string | null;
}

// =============================================================================
// Day 4: Calls Module Types
// =============================================================================

export type CallTerminationStatus = 'none' | 'requested' | 'confirmed' | 'failed';

export type CallTerminationRequestStatus =
    | 'confirmed'
    | 'requested'
    | 'failed'
    | 'already_terminal';

export interface TerminateCallResponse {
    status: CallTerminationRequestStatus;
    call_id: string;
    previous_status: string;
    new_status?: string | null;
    call_status?: string | null;
    termination_status: CallTerminationStatus;
    provider_hangup_requested: boolean;
    provider_hangup_confirmed: boolean;
    provider_hangup_error: string | null;
    termination_receipt_saved?: boolean;
    termination_reconciliation_error?: string | null;
    detail?: string;
}

export interface ContactEnquiry {
    id: string;
    name: string;
    email: string;
    company: string;
    message: string;
    status: 'new' | 'handled';
    created_at: string;
    handled_at: string | null;
    revision: number;
}

export interface ContactEnquiriesResponse {
    items: ContactEnquiry[];
    total: number;
    limit: number;
    offset: number;
}

export interface LiveCallItem {
    id: string;
    tenant_id: string;
    tenant_name: string;
    phone_number: string;
    campaign_name: string | null;
    status: 'queued' | 'dialing' | 'ringing' | 'answered' | 'in_call' | 'initiated' | string;
    started_at: string | null;
    duration_seconds: number;
    direction?: 'inbound' | 'outbound' | string;
    caller_ani?: string | null;
    called_did?: string | null;
    admission_status?: string | null;
    termination_status?: CallTerminationStatus | null;
    termination_requested_at?: string | null;
    termination_confirmed_at?: string | null;
    termination_reconciliation_error?: string | null;
    termination_error?: string | null;
    provider_hangup_requested?: boolean;
    provider_hangup_confirmed?: boolean;
}

export interface CallHistoryItem {
    id: string;
    tenant_id: string;
    tenant_name: string;
    phone_number: string;
    campaign_name: string | null;
    status: string;
    outcome: string | null;
    duration_seconds: number | null;
    started_at: string | null;
    ended_at: string | null;
    created_at: string;
    has_recording: boolean | null;
    has_feedback: boolean | null;
    feedback_transcript_status: string | null;
    direction?: 'inbound' | 'outbound' | string;
    caller_ani?: string | null;
    called_did?: string | null;
    admission_status?: string | null;
    admission_reason?: string | null;
    consent_status?: string | null;
    processing_status?: string | null;
    billing_status?: string | null;
    billing_hold_reason?: string | null;
}

export interface CallHistoryResponse {
    items: CallHistoryItem[];
    page: number;
    page_size: number;
    total: number;
}

export interface TimelineEvent {
    event: string;
    timestamp: string;
    status?: string;
}

export interface TranscriptTurn {
    role: 'assistant' | 'user';
    content: string;
    timestamp?: string;
}

export interface AdminTransferLeg {
    id: string;
    leg_type?: string | null;
    direction?: string | null;
    provider?: string | null;
    provider_leg_id?: string | null;
    from_number?: string | null;
    to_number?: string | null;
    status: string;
    started_at?: string | null;
    answered_at?: string | null;
    ended_at?: string | null;
    duration_seconds?: number | null;
    metadata?: Record<string, unknown> | null;
}

export interface AdminCRMReceipt {
    provider: 'hubspot' | 'salesforce';
    status: string;
    phase: string;
    destination_connector_id: string | null;
    destination_account_id: string | null;
    remote_contact_id: string | null;
    remote_call_id: string | null;
    updated_at: string | null;
    inspection_available: boolean;
}

export interface AdminCRMInspection {
    call_id: string;
    provider: AdminCRMReceipt['provider'];
    outcome: 'observed_reference' | 'no_match' | 'ambiguous' | 'unavailable';
    reason: 'reference_observed_only' | 'absence_is_inconclusive' | 'multiple_references'
        | 'original_receipt_unavailable' | 'original_authorization_unavailable' | 'provider_read_unavailable';
    observed_at: string;
    observed_remote_id: string | null;
}

export interface AdminCallDetail {
    id: string;
    tenant_id: string;
    tenant_name: string;
    phone_number: string;
    campaign_id: string | null;
    campaign_name: string | null;
    lead_id: string | null;
    status: string;
    outcome: string | null;
    goal_achieved: boolean;
    started_at: string | null;
    answered_at: string | null;
    ended_at: string | null;
    duration_seconds: number | null;
    transcript: string | null;
    transcript_json: TranscriptTurn[] | null;
    summary: string | null;
    summary_json: Record<string, unknown> | null;
    recording_url: string | null;
    cost: number | null;
    currency?: string | null;
    timeline: TimelineEvent[];
    created_at: string;
    updated_at?: string;
    direction?: 'inbound' | 'outbound' | string;
    provider?: string | null;
    provider_call_id?: string | null;
    caller_ani?: string | null;
    called_did?: string | null;
    ingress?: string | null;
    route_version?: number | null;
    config_version?: number | null;
    route_snapshot?: Record<string, unknown> | null;
    admission_status?: string | null;
    admission_reason?: string | null;
    consent_status?: string | null;
    processing_status?: string | null;
    billing_status?: string | null;
    billing_hold_reason?: string | null;
    reserved_seconds?: number | null;
    transfer_legs?: AdminTransferLeg[];
    crm_deliveries?: AdminCRMReceipt[];
    crm_receipts_available?: boolean;
    termination_status?: CallTerminationStatus | null;
    termination_requested_at?: string | null;
    termination_confirmed_at?: string | null;
    termination_reconciliation_error?: string | null;
    termination_error?: string | null;
    provider_hangup_requested?: boolean;
    provider_hangup_confirmed?: boolean;
}

export interface CallHistoryParams {
    page?: number;
    page_size?: number;
    search?: string;
    status?: string;
    tenant_id?: string;
    from_date?: string;
    to_date?: string;
    direction?: 'inbound' | 'outbound';
}

export interface AdminRecordingItem {
    id: string;
    call_id: string;
    tenant_id: string;
    tenant_name: string;
    phone_number: string;
    campaign_id: string | null;
    campaign_name: string | null;
    status: string;
    mime_type: string;
    storage: 'local' | 'object' | string;
    duration_seconds: number | null;
    file_size_bytes: number | null;
    created_at: string;
    updated_at: string | null;
    playable: boolean;
    direction?: 'inbound' | 'outbound' | string;
    caller_ani?: string | null;
    called_did?: string | null;
    legal_hold: boolean;
}

export interface AdminRecordingListResponse {
    items: AdminRecordingItem[];
    page: number;
    page_size: number;
    total: number;
}

export interface AdminFeedbackItem {
    id: string;
    call_id: string;
    tenant_id: string;
    tenant_name: string;
    phone_number: string;
    campaign_id: string | null;
    campaign_name: string | null;
    created_by: string | null;
    created_by_name: string | null;
    created_by_email: string | null;
    audio_mime_type: string;
    audio_size_bytes: number;
    duration_seconds: number | null;
    transcript: string | null;
    transcript_status: 'pending' | 'done' | 'failed' | string;
    transcript_error: string | null;
    transcription_attempts: number;
    transcript_provider: string | null;
    created_at: string;
    updated_at: string;
    retryable: boolean;
    direction?: 'inbound' | 'outbound' | string;
    caller_ani?: string | null;
    called_did?: string | null;
    legal_hold: boolean;
}

export interface AdminFeedbackListResponse {
    items: AdminFeedbackItem[];
    page: number;
    page_size: number;
    total: number;
}

export interface AdminMediaParams {
    page?: number;
    page_size?: number;
    search?: string;
    tenant_id?: string;
    call_id?: string;
    from_date?: string;
    to_date?: string;
    direction?: 'inbound' | 'outbound';
}

export interface AdminRecordingParams extends AdminMediaParams {
    status?: string;
}

export interface AdminFeedbackParams extends AdminMediaParams {
    transcript_status?: string;
}

export type AdminMediaResourceType = 'recording' | 'call_feedback';
export type AdminMediaDeletionStatus = 'intent_committed' | 'object_deleted' | 'failed' | 'completed';

export interface AdminMediaDeletionReceipt {
    detail: string;
    resource_type: AdminMediaResourceType;
    resource_id: string;
    outcome: 'completed' | 'absent';
    deletion_intent_id: string | null;
}

export interface AdminMediaDeletionItem {
    id: string;
    resource_type: AdminMediaResourceType;
    resource_id: string;
    tenant_id: string;
    call_id: string;
    original_actor_id: string;
    reason: string;
    status: AdminMediaDeletionStatus;
    attempt_count: number;
    created_at: string;
    updated_at: string;
    object_deleted_at: string | null;
    completed_at: string | null;
}

export interface AdminMediaDeletionParams {
    resource_type?: AdminMediaResourceType;
    resource_id?: string;
    tenant_id?: string;
    call_id?: string;
    status?: AdminMediaDeletionStatus | 'all';
    limit?: number;
    cursor?: string;
}

export interface AdminMediaDeletionList {
    items: AdminMediaDeletionItem[];
    has_more: boolean;
    next_cursor: string | null;
}

// =============================================================================
// Inbound operations control plane
// =============================================================================

export type InboundAssignmentStatus =
    | 'draft'
    | 'active'
    | 'paused'
    | 'quarantined'
    | 'archived'
    | 'released';

export interface AdminInboundRuntimeControls {
    inbound_enabled: boolean;
    recording_enabled: boolean;
    transfer_enabled: boolean;
    settlement_enabled: boolean;
    version: number;
    updated_at?: string | null;
    updated_by?: string | null;
    reason?: string | null;
}

export interface AdminInboundOverview {
    active_assignments: number;
    paused_assignments: number;
    quarantined_assignments: number;
    denied_last_24h: number;
    controls: AdminInboundRuntimeControls;
}

export interface AdminInboundAssignment {
    id: string;
    tenant_id: string;
    tenant_name: string;
    masked_did: string;
    campaign_id: string;
    campaign_name: string;
    trunk_id: string | null;
    status: InboundAssignmentStatus;
    version: number;
    config_version: number | null;
    readiness: {
        ready: boolean;
        checks: Array<{ key: string; label: string; passed: boolean; detail: string }>;
        blockers: Array<{ code: string; message: string; remediation: string }>;
    };
    last_call_at: string | null;
    last_error: string | null;
}

export interface AdminInboundAssignmentListResponse {
    items: AdminInboundAssignment[];
    total: number;
}

export interface AdminInboundAssignmentParams {
    tenant_id?: string;
    status?: string;
    search?: string;
}

export interface AdminInboundReassignmentRequest {
    id: string;
    assignment_id: string;
    approved_assignment_id?: string | null;
    source_tenant_id: string;
    target_tenant_id: string;
    target_campaign_id: string;
    status: 'pending' | 'approved' | 'rejected' | 'cancelled';
    requested_by: string;
    approved_by?: string | null;
    reason: string;
    decision_reason?: string | null;
    requested_at: string;
    decided_at?: string | null;
}

export interface AdminInboundReassignmentListResponse {
    items: AdminInboundReassignmentRequest[];
    has_more: boolean;
    next_cursor: string | null;
}

// =============================================================================
// Day 5: Actions Module Types
// =============================================================================

export type ActionType =
    | 'send_email'
    | 'send_sms'
    | 'initiate_call'
    | 'book_meeting'
    | 'set_reminder'
    | 'start_campaign';

export type ActionStatus =
    | 'pending'
    | 'running'
    | 'scheduled'
    | 'unknown'
    | 'completed'
    | 'failed'
    | 'cancelled';

export interface ActionItem {
    id: string;
    tenant_id: string;
    tenant_name: string;
    type: ActionType;
    status: ActionStatus;
    outcome_status: string | null;
    triggered_by: string | null;
    lead_name: string | null;
    lead_phone: string | null;
    error: string | null;
    created_at: string;
    started_at: string | null;
    completed_at: string | null;
    duration_ms: number | null;
}

export interface ActionListResponse {
    items: ActionItem[];
    total: number;
    page: number;
    page_size: number;
}

export interface EmailInspection {
    action_id: string;
    outcome: 'observed_message' | 'not_observed' | 'unavailable';
    reason: 'exact_message_observed_only' | 'absence_is_inconclusive' | 'saved_proof_unavailable'
        | 'original_authorization_unavailable' | 'provider_read_unavailable';
    observed_at: string;
    observed_message_id: string | null;
}

export interface CalendarInspection {
    action_id: string;
    outcome: 'observed_event' | 'not_observed' | 'unavailable';
    reason: 'exact_event_observed_only' | 'absence_is_inconclusive' | 'saved_proof_unavailable'
        | 'original_authorization_unavailable' | 'provider_read_unavailable';
    observed_at: string;
    observed_event_id: string | null;
}

export interface AcknowledgementRecoveryRequest {
    request_id: string;
    expected_source_digest: string;
    reason: string;
}

export interface AcknowledgementRecoveryCapability {
    source_digest: string;
    provider_status: 'accepted' | 'provider_accepted';
}

export interface AcknowledgementRecoveryRecord extends AcknowledgementRecoveryCapability {
    id: string;
    action_id: string;
    actor_id: string;
    actor_role: 'platform_admin';
    request_id: string;
    reason: string;
    original_status: 'unknown';
    recovered_status: 'completed';
    recorded_at: string;
}

const recoveryUuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const isRecoveryUuid = (value: unknown): value is string => typeof value === 'string' && recoveryUuid.test(value);
export const isRecoveryReason = (value: unknown): value is string => typeof value === 'string'
    && value.length > 0 && value.length <= 500 && value === value.trim()
    && Array.from(value).every((character) => {
        const code = character.charCodeAt(0);
        return code >= 32 && (code < 127 || code > 159);
    });

export function isAcknowledgementRecoveryCapability(value: unknown): value is AcknowledgementRecoveryCapability {
    const item = recordValue(value);
    return Boolean(item && typeof item.source_digest === 'string' && /^[a-f0-9]{64}$/.test(item.source_digest)
        && (item.provider_status === 'accepted' || item.provider_status === 'provider_accepted'));
}

export function isAcknowledgementRecoveryRecord(value: unknown, actionId: string): value is AcknowledgementRecoveryRecord {
    const item = recordValue(value);
    return Boolean(item && isAcknowledgementRecoveryCapability(item) && isRecoveryUuid(item.id)
        && isRecoveryUuid(item.action_id) && item.action_id === actionId && isRecoveryUuid(item.actor_id)
        && isRecoveryUuid(item.request_id) && item.actor_role === 'platform_admin' && isRecoveryReason(item.reason)
        && item.original_status === 'unknown' && item.recovered_status === 'completed'
        && typeof item.recorded_at === 'string' && item.recorded_at.length <= 64
        && Number.isFinite(Date.parse(item.recorded_at)));
}

export interface ActionDetail extends ActionItem {
    conversation_id: string | null;
    call_id: string | null;
    lead_id: string | null;
    campaign_id: string | null;
    campaign_name: string | null;
    connector_id: string | null;
    connector_name: string | null;
    input_data: Record<string, unknown> | null;
    output_data: Record<string, unknown> | null;
    email_inspection_available?: boolean;
    calendar_inspection_available?: boolean;
    acknowledgement_recovery?: AcknowledgementRecoveryCapability | null;
    acknowledgement_recovery_record?: AcknowledgementRecoveryRecord | null;
    saved_receipt?: {
        action_id: string;
        status: string;
        success: boolean;
        confirmation_allowed: boolean;
        receipt: Partial<Record<'identity_version' | 'tenant_id' | 'account_row_id' | 'provider' | 'connector_id' | 'external_account_id' | 'message_id'
            | 'external_event_id' | 'meeting_id' | 'job_id' | 'reminder_id' | 'plan_id'
            | 'child_action_id' | 'provider_status', string>>;
    } | null;
    ip_address: string | null;
    user_agent: string | null;
    request_id: string | null;
    idempotency_key: string | null;
    scheduled_at: string | null;
    is_retryable: boolean;
    is_cancellable: boolean;
}

export interface ActionListParams {
    page?: number;
    page_size?: number;
    search?: string;
    status?: string;
    type?: string;
    tenant_id?: string;
    from_date?: string;
    to_date?: string;
}

// =============================================================================
// Day 6: Connectors & Usage Types
// =============================================================================

export interface AdminConnectorItem {
    id: string;
    tenant_id: string;
    tenant_name: string;
    type: string; // calendar, email, crm, drive
    provider: string; // google_calendar, gmail, hubspot
    name: string | null;
    status: string; // pending, active, error, expired, disconnected
    account_email: string | null;
    token_expires_at: string | null;
    token_status: string; // valid, expiring_soon, expired, unknown
    last_refreshed_at: string | null;
    created_at: string;
    refresh_state?: 'idle' | 'in_flight' | 'uncertain';
    provider_revocation_status?: 'not_requested' | 'pending' | 'confirmed' | 'unconfirmed';
}

export interface AdminConnectorDetail extends AdminConnectorItem {
    scopes: string[];
    error_message: string | null;
    refresh_count: number;
}

export interface AdminConnectorListResponse {
    items: AdminConnectorItem[];
    total: number;
    page: number;
    page_size: number;
    summary?: {
        scope: 'all_connectors';
        total: number;
        active: number;
        expiring_or_expired: number;
        error: number;
    };
}

export interface ConnectorListParams {
    tenant_id?: string;
    status?: string;
    type?: string;
    provider?: string;
    page?: number;
    page_size?: number;
}

export interface LegacyOutboundEstimate {
    recorded_total: number | null;
    covered_call_count: number;
    missing_call_count: number;
    currency: 'USD';
    coverage: 'unavailable' | 'partial' | 'recorded_rows_only';
}

export interface UsageBreakdownItem {
    provider: string;
    usage_type: string;
    total_units: number;
    estimated_cost: number | null;
    tenant_count: number;
}

export interface UsageSummaryResponse {
    total_cost: number | null;
    supplier_cost_status: 'unavailable';
    total_call_seconds: number;
    total_call_minutes: number;
    total_action_records: number;
    total_api_calls: number; // Compatibility alias for action records.
    legacy_outbound_estimate: LegacyOutboundEstimate;
    providers: UsageBreakdownItem[];
    period_start: string;
    period_end: string;
    cost_currency: string;
    legacy_calls_cost_scope: 'outbound_only';
    authoritative_inbound_monetary_totals_included: false;
    authoritative_inbound_monetary_source: 'inbound_usage_transactions' | string;
    monetary_note: string;
}

export interface UsageBreakdownResponse {
    breakdown: Record<string, unknown>[];
    supplier_cost_status: 'unavailable';
    group_by: string;
    period_start: string;
    period_end: string;
    cost_currency: string;
    legacy_calls_cost_scope: 'outbound_only';
    authoritative_inbound_monetary_totals_included: false;
    authoritative_inbound_monetary_source: 'inbound_usage_transactions' | string;
    monetary_note: string;
}

export interface UsageParams {
    tenant_id?: string;
    from_date?: string;
    to_date?: string;
    group_by?: 'provider' | 'tenant' | 'type';
}

export interface TenantDetails extends TenantListItem {
    calling_rules: {
        time_window_start: string;
        time_window_end: string;
        timezone: string;
        max_concurrent_calls: number;
        retry_delay_seconds: number;
    };
    users: UserSummary[];
    recent_activity: {
        last_call: string | null;
        total_calls_this_month: number;
        active_campaigns: number;
    };
    updated_at?: string;
}

export interface UserQueryParams {
    page?: string;
    limit?: string;
    offset?: string;
    search?: string;
    tenant_id?: string;
    role?: string;
}

export interface UsersResponse {
    users: User[];
    pagination: Pagination;
}

export interface User {
    id: string;
    email: string;
    name: string | null;
    role: string;
    tenant_id: string;
    tenant_name: string;
    status: string;
    two_factor_enabled: boolean;
    last_active: string | null;
    created_at: string;
}

export interface UserDetails extends User {
    recent_activity: ActivityItem[];
    login_history: LoginHistoryItem[];
}

// =============================================================================
// Users & Roles management
//
// Backend contract (to be implemented on the API):
//   GET    /admin/users                 -> AdminUserItem[]   (rich list)
//   POST   /admin/users                 -> AdminUserItem     (create)
//   PATCH  /admin/users/{user_id}       -> AdminUserItem     (block/unblock, role, name)
//   DELETE /admin/users/{user_id}       -> { detail }        (delete)
//   GET    /rbac/roles                  -> RbacRole[]        (already exists)
//   GET    /rbac/permissions            -> RbacPermission[]  (already exists)
// =============================================================================

export type UserRoleName =
    | 'platform_admin'
    | 'partner_admin'
    | 'tenant_admin'
    | 'user'
    | 'readonly';

export interface AdminUserItem {
    id: string;
    email: string;
    name: string | null;
    role: string;
    effective_role?: string | null;
    membership_status?: string | null;
    tenant_id: string | null;
    tenant_name?: string | null;
    is_active?: boolean;        // false => blocked
    mfa_enabled?: boolean;
    is_verified?: boolean;
    last_login_at?: string | null;
    created_at?: string | null;
    review_token: string;
    audit_event_id?: string | null;
}

export interface UserMutationReceipt extends AdminUserItem {
    operation_id: string;
    operation: 'updated';
    audit_event_id: string;
}

export interface UserCreateState {
    request_id: string;
    state: 'unrecorded' | 'created' | 'cancelled';
    audit_event_id: string | null;
    user_id: string | null;
    user: AdminUserItem | null;
    intent: { name: string; email: string; role: string; tenant_id: string | null } | null;
}

export interface UserDeleteReceipt {
    operation_id: string;
    id: string;
    outcome: 'deleted' | 'deactivated';
    audit_event_id: string;
    detail: string;
    user: AdminUserItem | null;
}

export interface CreateUserRequest {
    operation_id: string;
    request_id: string;
    name: string;
    email: string;
    password: string;
    role: string;
    tenant_id?: string | null;
}

export interface UpdateUserRequest {
    operation_id: string;
    expected_review_token: string;
    name?: string;
    role?: string;
    expected_role?: string;
    is_active?: boolean;
}

export type UserOperationKind = 'create' | 'update' | 'delete' | 'primary_membership';
export interface UserOperationPrepare {
    kind: UserOperationKind;
    target_user_id: string | null;
    intent: Record<string, unknown>;
    password?: string;
}
export interface UserOperationView {
    operation_id: string;
    kind: UserOperationKind;
    target_user_id: string | null;
    intent: Record<string, unknown>;
    state: 'prepared' | 'committed' | 'cancelled';
    prepared_at: string;
    expires_at: string;
    dispatch_eligible: boolean;
    terminal_audit_event_id: string | null;
    receipt: unknown | null;
}
export interface UserOperationList { items: UserOperationView[]; next_cursor: string | null }
export interface PrimaryMembershipItem { id: string; role: string; status: string; is_primary: boolean }
export interface PrimaryMembershipReview {
    user: AdminUserItem;
    tenant: { id: string; name: string | null };
    membership: PrimaryMembershipItem | null;
    eligible: boolean;
    reason: string | null;
    review_token: string;
}
export interface PrimaryMembershipRequest {
    operation_id: string;
    tenant_id: string;
    role: string;
    expected_review_token: string;
}
export interface PrimaryMembershipReceipt {
    operation_id: string;
    operation: 'primary_membership_configured';
    user: AdminUserItem;
    membership: PrimaryMembershipItem;
    tenant_id: string;
    audit_event_id: string;
    credential_cutoff: { revoked_sessions: number; revoked_refresh_tokens: number; invalidated_mfa_challenges: number; invalidated_passkey_challenges: number };
}

export interface RbacRole {
    id: string;
    name: string;
    description: string | null;
    level: number;
    is_system_role: boolean;
    tenant_scoped: boolean;
}

export interface RbacPermission {
    id: string;
    name: string;
    description: string | null;
    resource: string;
    action: string;
}

export interface UserSummary {
    id: string;
    email: string;
    name: string;
    role: string;
    created_at: string;
}

export interface ActivityItem {
    action: string;
    timestamp: string;
    ip_address: string;
}

export interface LoginHistoryItem {
    timestamp: string;
    ip_address: string;
    user_agent: string;
    success: boolean;
}

export interface Pagination {
    page: number;
    limit: number;
    total: number;
    pages: number;
}

export interface AnalyticsParams {
    from?: string;
    to?: string;
    group_by?: string;
}

export interface SystemAnalytics {
    overview: {
        total_tenants: number;
        active_tenants: number;
        total_users: number;
        total_calls: number;
        total_minutes: number;
        revenue_this_month: number;
    };
    trends: {
        signups: { date: string; count: number }[];
        calls: { date: string; total: number; answered: number; failed: number }[];
        revenue: { date: string; amount: number }[];
    };
    top_tenants: { tenant_id: string; business_name: string; minutes_used: number; calls_made: number }[];
}

export interface ProviderAnalytics {
    providers: {
        type: string;
        name: string;
        status: string;
        avg_latency_ms: number;
        error_rate: number;
        total_requests: number;
        successful_requests: number;
        failed_requests: number;
        uptime_percentage: number;
    }[];
    trends: {
        latency: { timestamp: string; provider: string; latency_ms: number }[];
        error_rate: { timestamp: string; provider: string; error_rate: number }[];
    };
}

export interface AuditQueryParams {
    page?: string;
    limit?: string;
    action_type?: string;
    user_id?: string;
    tenant_id?: string;
    from?: string;
    to?: string;
}

export interface AuditResponse {
    audit_entries: AuditEntry[];
    pagination: Pagination;
}

export interface AuditEntry {
    id: string;
    tenant_id: string;
    action_type: string;
    triggered_by: string;
    outcome_status: string;
    input_data: unknown;
    output_data: unknown;
    error: string | null;
    user_id: string | null;
    ip_address: string | null;
    created_at: string;
}

export interface SecurityQueryParams {
    from?: string;
    to?: string;
    severity?: string;
    acknowledged?: string;
}

export interface SecurityEventsResponse {
    events: SecurityEvent[];
    summary: {
        total_events: number;
        critical: number;
        high: number;
        medium: number;
        low: number;
        unacknowledged: number;
    };
}

export interface SecurityEvent {
    id: string;
    type: string;
    severity: string;
    title: string;
    message: string;
    metadata: unknown;
    acknowledged: boolean;
    acknowledged_by: string | null;
    acknowledged_at: string | null;
    created_at: string;
}

export interface SystemConfiguration {
    providers: {
        stt: ProviderConfig;
        tts: ProviderConfig;
        llm: ProviderConfig;
        telephony: ProviderConfig;
    };
    features: {
        websocket_enabled: boolean;
        analytics_enabled: boolean;
        billing_enabled: boolean;
        quota_enforcement: boolean;
    };
    limits: {
        max_tenants: number;
        max_users_per_tenant: number;
        max_concurrent_calls: number;
        max_campaigns_per_tenant: number;
    };
}

export interface ProviderConfig {
    active: string;
    available: string[];
    config: Record<string, unknown>;
}

export interface HealthStatus {
    status: string;
    database: string;
    cache: string;
    providers: {
        stt: string;
        tts: string;
        llm: string;
        telephony: string;
    };
}

// Command Center Types
export interface DashboardStats {
    active_calls: number;
    failed_call_rate_24h: string | null;
    active_tenants: number;
    failed_calls_24h: number;
    total_calls_24h: number;
    observed_at: string;
}

export interface SystemHealthItem {
    name: string;
    status: 'operational' | 'degraded' | 'down' | 'unknown' | 'observed_success' | 'observed_failure';
    latency_ms: number | null;
    latency_display: string;
    last_check: string | null;
    scope: string;
    detail: string;
}

export interface SystemHealthResponse {
    providers: SystemHealthItem[];
}

export interface PauseCallsResponse {
    paused: boolean;
    paused_at: string | null;
    paused_by: string | null;
    reason: string | null;
    message: string;
}

// =============================================================================
// Day 8: Enhanced System Health Types
// =============================================================================

export interface DetailedHealthResponse {
    uptime_seconds: number;
    uptime_display: string;
    memory_usage_mb: number;
    memory_total_mb: number;
    memory_percent: number;
    cpu_usage_percent: number;
    disk_usage_percent: number;
    disk_free_gb: number;
    os_info: string;
    python_version: string;
    version: string;
    environment: string;
    providers: SystemHealthItem[];
    checked_at: string;
    last_updated: string;
    metrics_scope: string;
}

export interface WorkerStatus {
    id: string;
    name: string;
    status: 'healthy' | 'offline' | 'unknown';
    current_task: string | null;
    processed_count: number | null;
    failed_count: number | null;
    success_rate: number | null;
    uptime_seconds: number | null;
    last_heartbeat: string | null;
    age_seconds: number | null;
    detail: string;
}

export interface WorkersResponse {
    workers: WorkerStatus[];
    total_workers: number;
    active_workers: number;
    busy_workers: number | null;
    checked_at: string;
    scope: string;
}

export interface QueueStatus {
    name: string;
    pending: number | null;
    processing: number | null;
    failed: number | null;
    completed_24h: number | null;
    avg_processing_time_ms: number | null;
    success_rate_24h: number | null;
    status: 'measured' | 'unavailable';
    scope: string;
    detail: string;
}

export interface QueuesResponse {
    queues: QueueStatus[];
    total_pending: number | null;
    total_processing: number | null;
    checked_at: string;
}

export interface DatabaseHealthResponse {
    connected: boolean;
    latency_ms: number | null;
    pool_size: number | null;
    active_connections: number | null;
    available_connections: number | null;
    database_size_mb: number | null;
    table_count: number | null;
    last_check: string;
    error: string | null;
    scope: string;
}

export interface IncidentItem {
    id: string;
    title: string;
    severity: 'critical' | 'warning' | 'info';
    status: 'open' | 'acknowledged' | 'resolved';
    description: string | null;
    triggered_at: string;
    acknowledged_at: string | null;
    acknowledged_by: string | null;
    resolved_at: string | null;
    resolved_by: string | null;
    source_system?: string | null;
    source_fingerprint?: string | null;
    source_started_at?: string | null;
    source_status?: 'firing' | 'recovered' | null;
    source_recovered_at?: string | null;
    source_last_received_at?: string | null;
    source_labels?: Record<string, string>;
}

export interface IncidentsResponse {
    items: IncidentItem[];
    total: number;
    page: number;
    page_size: number;
    open_count: number;
    critical_count: number;
}

export interface IncidentTransitionReceipt {
    success: boolean;
    message: string;
    incident_id: string;
    status: IncidentItem['status'];
    acknowledged_at: string | null;
    acknowledged_by: string | null;
    resolved_at: string | null;
    resolved_by: string | null;
}

export interface IncidentsParams {
    page?: number;
    page_size?: number;
    status?: 'open' | 'acknowledged' | 'resolved';
    severity?: 'critical' | 'warning' | 'info';
}

export interface AlertSettings {
    management: 'external';
    source: 'alertmanager';
    editable: false;
    ingestion_status: 'configured' | 'unavailable';
    notification_delivery: 'unverified';
    detail: string;
}

// API Response wrapper
interface ApiResponse<T> {
    data?: T;
    error?: {
        code: string;
        message: string;
        status?: number;
        details?: unknown;
        // Local coordination evidence only; never accepted from an HTTP payload.
        sessionRequestNotSent?: true;
    };
}

type AdminApiError = NonNullable<ApiResponse<unknown>['error']>;

function recordValue(value: unknown): Record<string, unknown> | null {
    return value !== null && typeof value === 'object' && !Array.isArray(value)
        ? value as Record<string, unknown>
        : null;
}

/**
 * FastAPI endpoints currently return both the canonical
 * `{ error: { code, message, details } }` envelope and the legacy
 * `{ detail: string | { code, message, readiness } }` envelope. Keep the
 * Admin UI on one safe string-only contract until the backend migration is
 * complete; React must never receive `detail` as a renderable object.
 */
export function normalizeAdminApiError(payload: unknown): AdminApiError {
    const root = recordValue(payload) ?? {};
    const error = recordValue(root.error);
    const detail = recordValue(root.detail);
    const source = error ?? detail ?? root;
    // FastAPI's confirmation-aware termination errors use
    // `detail.error`/`detail.reason` rather than `detail.code`. Preserve that
    // stable machine code instead of collapsing a useful provider failure to
    // UNKNOWN_ERROR.
    const rawCode = source.code ?? source.error ?? source.reason ?? root.code;
    const code = (typeof rawCode === 'string' ? rawCode : 'unknown_error')
        .trim()
        .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
        .replace(/[^a-zA-Z0-9]+/g, '_')
        .replace(/^_+|_+$/g, '')
        .toUpperCase() || 'UNKNOWN_ERROR';
    const candidates = [
        source.message,
        source.provider_hangup_error,
        source.reason,
        typeof source.error === 'string' ? source.error : undefined,
        root.message,
        typeof root.detail === 'string' ? root.detail : undefined,
        typeof root.error === 'string' ? root.error : undefined,
    ];
    const message = candidates.find((value): value is string => (
        typeof value === 'string' && value.trim().length > 0
    ))?.trim() ?? 'An error occurred';
    const details = error?.details
        ?? source.readiness
        ?? root.details
        ?? (detail ? root.detail : undefined);

    return { code, message, details };
}

interface RequestOptions {
    method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
    body?: unknown;
    headers?: Record<string, string>;
    timeoutMs?: number;
    // Logout may wait behind another tab while its own UI retires credentials.
    // Keep the reviewed caller's headers; never adopt a replacement account.
    authSnapshot?: { token: string | null; headers: Record<string, string> };
}

type SessionIdentity = { userId: string; tenantId?: string | null; role?: string };
type RefreshedSession = { access_token: string; user_id: string; tenant_id?: string | null; role: string };
const sessionChangedError = { code: 'IDENTITY_CHANGED', message: 'Your sign-in changed. Reload the current view before continuing.', status: 409 };
const publicAuthPath = (path: string) => ['/auth/login', '/auth/mfa/verify', '/auth/passkey-check',
    '/auth/passkeys/login/begin', '/auth/passkeys/login/complete'].includes(path);
const managesSession = (path: string) => publicAuthPath(path) || ['/auth/logout', '/auth/refresh'].includes(path);

/** A stored JWT supplies consistency hints only; /me and /refresh still verify authority. */
function storedIdentity(token: string | null): SessionIdentity | null {
    try {
        if (!token) return null;
        const payload = token.split('.')[1];
        const claims = JSON.parse(atob(payload.replace(/-/g, '+').replace(/_/g, '/')));
        if (typeof claims.sub !== 'string' || !claims.sub) return null;
        return { userId: claims.sub, tenantId: typeof claims.tenant_id === 'string' ? claims.tenant_id : null };
    } catch { return null; }
}

// API Client Class
class ApiClient {
    private baseUrl: string;
    private token: string | null = null;
    private authGeneration = 0;
    private expectedIdentity: SessionIdentity | null = null;
    private refreshInFlight: { generation: number; promise: Promise<ApiResponse<RefreshedSession>> } | null = null;
    private refreshListeners = new Set<(role: string, authorityChanged: boolean) => void>();
    private authorityCheckInFlight: { generation: number; token: string | null; promise: Promise<void> } | null = null;
    private authorityUnavailableListeners = new Set<(message: string) => void>();

    constructor(baseUrl: string) {
        this.baseUrl = baseUrl;
        // Try to get token from localStorage on init
        if (typeof window !== 'undefined') {
            try { this.token = localStorage.getItem('admin_token'); } catch { /* Cookie auth remains available. */ }
        }
    }

    private authGenerationListeners = new Set<() => void>();

    onAuthGenerationChanged(listener: () => void): () => void {
        this.authGenerationListeners.add(listener);
        return () => { this.authGenerationListeners.delete(listener); };
    }

    setToken(token: string | null, persist = true) {
        const changed = this.token !== token;
        if (changed) { this.authGeneration += 1; this.expectedIdentity = null; }
        this.token = token;
        if (persist) this.persistToken();
        if (changed) for (const listener of [...this.authGenerationListeners]) listener();
    }

    /** Cookie-only identities also need an epoch boundary even when no token changed. */
    invalidateIdentity(persist = true) {
        this.authGeneration += 1;
        this.expectedIdentity = null;
        this.token = null;
        if (persist) this.persistToken();
        for (const listener of [...this.authGenerationListeners]) listener();
    }

    private persistToken() {
        try {
            if (this.token) localStorage.setItem('admin_token', this.token);
            else localStorage.removeItem('admin_token');
        } catch { /* A blocked storage area must not prevent in-memory/cookie authentication. */ }
    }

    expectIdentity(identity: SessionIdentity | null) { this.expectedIdentity = identity; }

    onSessionRefreshed(listener: (role: string, authorityChanged: boolean) => void): () => void {
        this.refreshListeners.add(listener);
        return () => { this.refreshListeners.delete(listener); };
    }

    onAuthorityUnavailable(listener: (message: string) => void): () => void {
        this.authorityUnavailableListeners.add(listener);
        return () => { this.authorityUnavailableListeners.delete(listener); };
    }

    private identityHeaders(): Record<string, string> {
        const identity = this.expectedIdentity ?? storedIdentity(this.token);
        if (!identity) return {};
        return { 'X-Talky-Expected-User': identity.userId,
            ...(identity.tenantId !== undefined ? { 'X-Talky-Expected-Tenant': identity.tenantId ?? '' } : {}) };
    }

    getToken(): string | null {
        return this.token;
    }

    getAuthGeneration(): number {
        return this.authGeneration;
    }

    private unauthorizedListeners = new Set<() => void>();

    /** Called once when a 401 shows the current session expired or was revoked. */
    onUnauthorized(listener: () => void): () => void {
        this.unauthorizedListeners.add(listener);
        return () => { this.unauthorizedListeners.delete(listener); };
    }

    // A 401 on an authenticated call means the session is gone: drop the dead
    // token and tell the AuthProvider. Skipped for the credential/identity
    // calls (a 401 there means bad credentials, or is handled by checkAuth,
    // which would otherwise lose its own result) and for a response that
    // belongs to a previous session (a newer login must survive a late 401).
    private handleSessionRejected(endpoint: string, sentGeneration: number, sentToken: string | null) {
        const path = endpoint.split('?')[0];
        if (managesSession(path) || path === '/auth/me') return;
        if (sentGeneration !== this.authGeneration || sentToken !== this.token) return;
        this.invalidateIdentity();
        for (const listener of [...this.unauthorizedListeners]) listener();
    }

    /** A forbidden operation may reveal a changed role, but is not itself a logout.
     * Verify current authority once, without replaying the rejected operation or
     * recursively inspecting /me. Unchanged permissions remain an ordinary403.
     */
    private recheckForbiddenAuthority(endpoint: string, generation: number, token: string | null): Promise<void> {
        const path = endpoint.split('?')[0];
        const current = () => generation === this.authGeneration && token === this.token;
        if (managesSession(path) || path === '/auth/me' || !current()) return Promise.resolve();
        // There must be a prior server-verified role to reconsider. Otherwise a
        // denied unverified view could remount itself on each rejected /me.
        const expected = this.expectedIdentity;
        if (!expected?.role) return Promise.resolve();
        if (this.authorityCheckInFlight?.generation === generation && this.authorityCheckInFlight.token === token) {
            return this.authorityCheckInFlight.promise;
        }
        const pending = (async () => {
            const result = await this.request<MeResponse>('/auth/me');
            if (!current()) return;
            if (result.error) {
                if ([401, 403, 409].includes(result.error.status ?? 0) && result.error.code !== 'SESSION_RENEWAL_UNAVAILABLE') {
                    this.handleSessionRejected(endpoint, generation, token);
                } else {
                    for (const listener of [...this.authorityUnavailableListeners]) listener(result.error.message);
                }
                return;
            }
            const value = result.data;
            if (!value || typeof value.id !== 'string' || !value.id || typeof value.email !== 'string'
                || typeof value.role !== 'string' || !value.role.trim()
                || (value.tenant_id != null && typeof value.tenant_id !== 'string')) {
                for (const listener of [...this.authorityUnavailableListeners]) listener('The server returned an incomplete identity.');
                return;
            }
            if (expected && (value.id !== expected.userId
                || (expected.tenantId !== undefined && (value.tenant_id ?? null) !== expected.tenantId))) {
                this.handleSessionRejected(endpoint, generation, token);
                return;
            }
            this.expectedIdentity = { userId: value.id, tenantId: value.tenant_id ?? null, role: value.role };
            if (expected?.role && expected.role !== value.role) {
                // The bearer may be unchanged. Still retire every old-role view
                // and use the existing canonical AuthProvider verification flow.
                this.authGeneration += 1;
                for (const listener of [...this.authGenerationListeners]) listener();
                for (const listener of [...this.refreshListeners]) listener(value.role, true);
            }
        })();
        this.authorityCheckInFlight = { generation, token, promise: pending };
        void pending.finally(() => {
            if (this.authorityCheckInFlight?.promise === pending) this.authorityCheckInFlight = null;
        });
        return pending;
    }

    /** Serialize cookie-changing transports across same-origin Admin/customer tabs.
     * Without this browser capability, do not dispatch an unordered update.
     * Different frontend origins remain outside this coordination boundary.
     */
    private async withSessionLock<T>(run: () => Promise<ApiResponse<T>>): Promise<ApiResponse<T>> {
        const controller = new AbortController();
        let timeout: ReturnType<typeof setTimeout> | undefined;
        let admitted = false;
        try {
            // Lookup itself can fail when browser storage access is restricted.
            const locks = typeof navigator !== 'undefined' ? navigator.locks : undefined;
            if (!locks || typeof locks.request !== 'function') throw new Error('Session coordination unavailable');
            // Match Talk-Leee/browser-session.ts. Equivalent relative/absolute
            // configuration must identify the same API; do not guess proxy aliases.
            const url = new URL(this.baseUrl, window.location.origin);
            if (!/^https?:$/.test(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error('Invalid session API URL');
            const name = `talky-admin-session:${url.origin}${url.pathname.replace(/\/+$/, '')}`;
            timeout = setTimeout(() => controller.abort(), 20000);
            return await locks.request(name, { signal: controller.signal }, () => {
                admitted = true;
                // Web Locks aborts only a queued acquisition. Once admitted,
                // the request transport owns its deadline and outcome.
                clearTimeout(timeout);
                return run();
            });
        } catch {
            if (admitted) return { error: { code: 'SESSION_UPDATE_UNCONFIRMED',
                message: 'The browser session update could not be confirmed. Check your session before trying again.' } };
            return { error: {
                code: controller.signal.aborted ? 'SESSION_COORDINATION_TIMEOUT' : 'SESSION_COORDINATION_UNAVAILABLE',
                message: controller.signal.aborted
                    ? 'The session request was not sent because another browser session update is still running. Wait for it to finish, then try again.'
                    : 'The session request was not sent because this page cannot coordinate browser sessions. Open Admin over HTTPS in a supported browser with site storage enabled, then try again.',
                sessionRequestNotSent: true,
            } };
        } finally { clearTimeout(timeout); }
    }

    private credentialRequest<T>(endpoint: string, body: unknown, ownsOperation?: () => boolean): Promise<ApiResponse<T>> {
        const generation = this.authGeneration;
        const token = this.token;
        return this.withSessionLock(() => {
            if (generation !== this.authGeneration || token !== this.token || (ownsOperation && !ownsOperation())) {
                return Promise.resolve({ error: sessionChangedError });
            }
            return this.request<T>(endpoint, { method: 'POST', body });
        });
    }

    /** One rotation per tab; the shared lock also orders login/MFA/logout headers. */
    private renewSession(generation: number, sentToken: string | null): Promise<ApiResponse<RefreshedSession>> {
        if (generation !== this.authGeneration) return Promise.resolve({ error: sessionChangedError });
        if (this.refreshInFlight?.generation === generation) return this.refreshInFlight.promise;
        const expected = this.expectedIdentity ?? storedIdentity(sentToken);
        const run = async (): Promise<ApiResponse<RefreshedSession>> => {
            if (generation !== this.authGeneration) return { error: sessionChangedError };
            // A sibling request in this verified identity already rotated it.
            if (sentToken !== this.token && this.token && this.expectedIdentity) {
                return { data: { access_token: this.token, user_id: this.expectedIdentity.userId,
                    tenant_id: this.expectedIdentity.tenantId, role: this.expectedIdentity.role ?? '' } };
            }
            const result = await this.request<RefreshedSession>('/auth/refresh', { method: 'POST', timeoutMs: 10000 }, false);
            if (generation !== this.authGeneration) return { error: sessionChangedError };
            if (result.error) return result;
            const value = result.data;
            if (!value || typeof value.access_token !== 'string' || !value.access_token
                || typeof value.user_id !== 'string' || !value.user_id || typeof value.role !== 'string' || !value.role) {
                return { error: { code: 'INVALID_RESPONSE', message: 'Session renewal could not be confirmed. Please sign in again.' } };
            }
            if (expected && (value.user_id !== expected.userId
                || (expected.tenantId !== undefined && (value.tenant_id ?? null) !== expected.tenantId))) {
                return { error: sessionChangedError };
            }
            // Routine token rotation preserves operation ownership. A role change
            // retires privileged views just as a sign-in change does.
            const authorityChanged = Boolean(expected?.role && expected.role !== value.role);
            if (authorityChanged) this.setToken(value.access_token);
            else { this.token = value.access_token; this.persistToken(); }
            this.expectedIdentity = { userId: value.user_id, tenantId: value.tenant_id ?? null, role: value.role };
            for (const listener of [...this.refreshListeners]) listener(value.role, authorityChanged);
            return result;
        };
        const pending = this.withSessionLock(run);
        this.refreshInFlight = { generation, promise: pending };
        void pending.finally(() => {
            if (this.refreshInFlight?.promise === pending) this.refreshInFlight = null;
        });
        return pending;
    }

    private async request<T>(endpoint: string, options: RequestOptions = {}, mayRefresh = true): Promise<ApiResponse<T>> {
        const { method = 'GET', body, headers = {}, timeoutMs = 20000, authSnapshot } = options;
        const path = endpoint.split('?')[0];
        const controller = timeoutMs ? new AbortController() : undefined;
        const timer = controller ? setTimeout(() => controller.abort(), timeoutMs) : undefined;

        const requestHeaders: Record<string, string> = {
            'Content-Type': 'application/json',
            ...(!publicAuthPath(path) ? authSnapshot?.headers ?? this.identityHeaders() : {}),
            ...headers,
        };

        const requestToken = authSnapshot ? authSnapshot.token : this.token;
        if (requestToken && !publicAuthPath(path) && path !== '/auth/refresh') {
            requestHeaders['Authorization'] = `Bearer ${requestToken}`;
        }
        const sentGeneration = this.authGeneration;
        const sentToken = requestToken;

        try {
            const response = await fetch(`${this.baseUrl}${endpoint}`, {
                method,
                headers: requestHeaders,
                body: body ? JSON.stringify(body) : undefined,
                signal: controller?.signal,
                credentials: 'include',
            });

            // Gateways may return HTML or an empty body. Preserve the HTTP
            // status even when JSON decoding fails, especially a rejected session.
            let data: unknown;
            try { data = await response.json(); }
            catch {
                if (response.ok && response.status !== 204) {
                    return { error: { code: 'INVALID_RESPONSE', message: 'The server returned an invalid response. Refresh to check the current state.', status: response.status } };
                }
                data = { detail: `Request failed (${response.status})` };
            }

            if (!response.ok) {
                const error = { ...normalizeAdminApiError(data), status: response.status };
                if (!managesSession(path) && sentGeneration !== this.authGeneration) return { error: sessionChangedError };
                if (response.status === 401 && mayRefresh && !managesSession(path)) {
                    const refreshed = await this.renewSession(sentGeneration, sentToken);
                    if (refreshed.data) {
                        // Never replay a mutation, even when authentication rejected it.
                        if (method === 'GET') return this.request<T>(endpoint, options, false);
                        return { error: { code: 'AUTH_RETRY_REQUIRED', status: 401,
                            message: 'Your session was renewed. Review the current state, then confirm the action again.' } };
                    }
                    if (refreshed.error?.code === 'IDENTITY_CHANGED') this.handleSessionRejected(endpoint, sentGeneration, sentToken);
                    if (refreshed.error?.status !== 401) return { error: refreshed.error?.status === 403
                        ? { ...refreshed.error, code: 'SESSION_RENEWAL_UNAVAILABLE' } : refreshed.error ?? error };
                }
                if (response.status === 401) this.handleSessionRejected(endpoint, sentGeneration, sentToken);
                if (response.status === 409 && error.code === 'IDENTITY_CHANGED') this.handleSessionRejected(endpoint, sentGeneration, sentToken);
                if (response.status === 403) await this.recheckForbiddenAuthority(endpoint, sentGeneration, sentToken);
                return { error };
            }

            if (!managesSession(path) && sentGeneration !== this.authGeneration) return { error: sessionChangedError };
            return { data: data as T };
        } catch (error) {
            return {
                error: {
                    code: controller?.signal.aborted ? 'REQUEST_TIMEOUT' : 'NETWORK_ERROR',
                    message: controller?.signal.aborted
                        ? 'The request timed out. Refresh to check the current state before retrying.'
                        : error instanceof Error ? error.message : 'Network error occurred',
                },
            };
        } finally {
            if (timer !== undefined) clearTimeout(timer);
        }
    }

    private async requestBlob(endpoint: string, mayRefresh = true): Promise<Blob> {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 30000);
        try {
        const headers: Record<string, string> = this.identityHeaders();
        if (this.token) {
            headers.Authorization = `Bearer ${this.token}`;
        }
        const sentGeneration = this.authGeneration;
        const sentToken = this.token;
        const response = await fetch(`${this.baseUrl}${endpoint}`, { headers, signal: controller.signal, credentials: 'include' });
        if (sentGeneration !== this.authGeneration) throw new Error(sessionChangedError.message);
        if (!response.ok) {
            if (response.status === 401 && mayRefresh) {
                const refreshed = await this.renewSession(sentGeneration, sentToken);
                if (refreshed.data) return this.requestBlob(endpoint, false);
                if (refreshed.error?.code === 'IDENTITY_CHANGED') this.handleSessionRejected(endpoint, sentGeneration, sentToken);
                if (refreshed.error?.status !== 401) throw new Error(refreshed.error?.message ?? 'Session renewal unavailable');
            }
            if (response.status === 401) this.handleSessionRejected(endpoint, sentGeneration, sentToken);
            if (response.status === 403) await this.recheckForbiddenAuthority(endpoint, sentGeneration, sentToken);
            let message = `Media request failed (${response.status})`;
            try {
                const payload: unknown = await response.json();
                const normalized = normalizeAdminApiError(payload);
                if (response.status === 409 && normalized.code === 'IDENTITY_CHANGED') this.handleSessionRejected(endpoint, sentGeneration, sentToken);
                message = normalized.message === 'An error occurred' ? message : normalized.message;
            } catch {
                // Non-JSON storage/provider errors retain the status message.
            }
            throw new Error(message);
        }
        const blob = await response.blob();
        if (sentGeneration !== this.authGeneration) throw new Error(sessionChangedError.message);
        return blob;
        } finally { clearTimeout(timer); }
    }

    // Auth Endpoints
    async login(email: string, password: string, ownsOperation?: () => boolean) {
        return this.credentialRequest<LoginResponse>('/auth/login', { email, password }, ownsOperation);
    }

    async verifyMfaChallenge(challengeToken: string, factor: { code: string } | { recovery_code: string }, ownsOperation?: () => boolean) {
        return this.credentialRequest<LoginResponse>('/auth/mfa/verify', { challenge_token: challengeToken, ...factor }, ownsOperation);
    }

    // The backend has no /auth/verify — the current user is read from /auth/me.
    // We normalise it into the { valid, user } shape the AuthProvider expects.
    async verifyToken(): Promise<ApiResponse<{ valid: boolean; user: AdminUser }>> {
        const res = await this.request<MeResponse>('/auth/me');
        if (res.error || !res.data) {
            return { error: res.error ?? { code: 'AUTH_FAILED', message: 'Not authenticated' } };
        }
        const me = res.data;
        if (typeof me.id !== 'string' || !me.id || typeof me.email !== 'string' || typeof me.role !== 'string' || !me.role) {
            return { error: { code: 'INVALID_RESPONSE', message: 'The server could not verify your identity.' } };
        }
        const expected = this.expectedIdentity;
        if (expected && (me.id !== expected.userId || (expected.tenantId !== undefined && (me.tenant_id ?? null) !== expected.tenantId))) {
            return { error: sessionChangedError };
        }
        this.expectedIdentity = { userId: me.id, tenantId: me.tenant_id ?? null, role: me.role };
        return {
            data: {
                valid: true,
                user: {
                    id: me.id,
                    email: me.email,
                    name: me.name ?? me.email,
                    role: me.role,
                    tenant_id: me.tenant_id ?? undefined,
                },
            },
        };
    }

    async logout(ownsOperation?: () => boolean) {
        const authSnapshot = { token: this.token, headers: this.identityHeaders() };
        return this.withSessionLock(() => {
            if ((ownsOperation && !ownsOperation()) || (this.token !== null && this.token !== authSnapshot.token)) {
                return Promise.resolve({ error: sessionChangedError });
            }
            return this.request('/auth/logout', { method: 'POST', authSnapshot });
        });
    }

    // Admin Endpoints
    // Note: getTenants, getTenantById, updateTenantQuota, suspendTenant, resumeTenant
    // are defined below in the "Tenant Management Endpoints (Day 3)" section

    async getUsers(params?: UserQueryParams) {
        const query = params ? '?' + new URLSearchParams(params as Record<string, string>).toString() : '';
        return this.request<UsersResponse>(`/admin/users${query}`);
    }

    async getUser(userId: string) {
        return this.request<AdminUserItem>(`/admin/users/${userId}`);
    }

    async getUserCreateState(requestId: string) {
        return this.request<UserCreateState>(`/admin/users/create-requests/${requestId}`);
    }

    async cancelUserCreate(requestId: string) {
        return this.request<UserCreateState>(`/admin/users/create-requests/${requestId}/cancel`, { method: 'POST' });
    }

    async prepareUserOperation(body: UserOperationPrepare) {
        return this.request<UserOperationView>('/admin/users/operations', { method: 'POST', body });
    }

    async getUserOperations(state: 'pending' | 'terminal', cursor?: string) {
        const query = new URLSearchParams({ state, limit: '25' });
        if (cursor) query.set('cursor', cursor);
        return this.request<UserOperationList>(`/admin/users/operations?${query}`);
    }

    async getUserOperation(operationId: string) {
        return this.request<UserOperationView>(`/admin/users/operations/${operationId}`);
    }

    async cancelUserOperation(operationId: string) {
        return this.request<UserOperationView>(`/admin/users/operations/${operationId}/cancel`, { method: 'POST' });
    }

    async getPrimaryMembershipReview(userId: string, tenantId: string) {
        return this.request<PrimaryMembershipReview>(`/admin/users/${userId}/primary-membership?${new URLSearchParams({ tenant_id: tenantId })}`);
    }

    async configurePrimaryMembership(userId: string, body: PrimaryMembershipRequest) {
        return this.request<PrimaryMembershipReceipt>(`/admin/users/${userId}/primary-membership`, { method: 'POST', body });
    }

    // --- Users & Roles management ---------------------------------------------

    // The backend returns a bare array for /admin/users (not the wrapped
    // UsersResponse), so this is the shape the page actually consumes.
    // Build the query manually — passing undefined values through
    // URLSearchParams would serialize them as the literal "undefined" and the
    // backend's ILIKE filter would then match nothing.
    async getUsersList(params?: UserQueryParams) {
        const sp = new URLSearchParams();
        if (params?.search) sp.append('search', params.search);
        if (params?.role) sp.append('role', params.role);
        if (params?.limit) sp.append('limit', params.limit);
        if (params?.offset) sp.append('offset', params.offset);
        const query = sp.toString() ? `?${sp.toString()}` : '';
        return this.request<AdminUserItem[]>(`/admin/users${query}`);
    }

    async createUser(payload: CreateUserRequest) {
        return this.request<UserCreateState>('/admin/users', {
            method: 'POST',
            body: payload,
        });
    }

    // Block/unblock (is_active), rename, or change role.
    async updateUser(userId: string, payload: UpdateUserRequest) {
        return this.request<UserMutationReceipt>(`/admin/users/${userId}`, {
            method: 'PATCH',
            body: payload,
        });
    }

    async deleteUser(userId: string, expectedReviewToken: string, operationId: string) {
        return this.request<UserDeleteReceipt>(`/admin/users/${userId}`, {
            method: 'DELETE',
            body: { expected_review_token: expectedReviewToken, operation_id: operationId },
        });
    }

    async getRoles() {
        return this.request<RbacRole[]>('/rbac/roles');
    }

    async getPermissions() {
        return this.request<RbacPermission[]>('/rbac/permissions');
    }

    // Analytics Endpoints
    async getSystemAnalytics(params?: AnalyticsParams) {
        const query = params ? '?' + new URLSearchParams(params as Record<string, string>).toString() : '';
        return this.request<SystemAnalytics>(`/analytics/system${query}`);
    }

    async getProviderAnalytics(params?: AnalyticsParams) {
        const query = params ? '?' + new URLSearchParams(params as Record<string, string>).toString() : '';
        return this.request<ProviderAnalytics>(`/analytics/providers${query}`);
    }

    // Audit Endpoints
    // Backend mounts audit logs at /admin/audit/logs (the bare /admin/audit 404s).
    async getAuditLog(params?: AuditQueryParams) {
        const query = params ? '?' + new URLSearchParams(params as Record<string, string>).toString() : '';
        return this.request<AuditResponse>(`/admin/audit/logs${query}`);
    }

    // Backend route is GET /admin/security-events/events (router prefix
    // /admin/security-events + /events path), not /admin/security/events.
    async getSecurityEvents(params?: SecurityQueryParams) {
        const query = params ? '?' + new URLSearchParams(params as Record<string, string>).toString() : '';
        return this.request<SecurityEventsResponse>(`/admin/security-events/events${query}`);
    }

    // Configuration Endpoints
    // NOTE: there is no /admin/configuration endpoint on the backend yet, so these
    // will 404 until one is added. SystemConfigPage is not currently routed in App.tsx.
    async getConfiguration() {
        return this.request<SystemConfiguration>('/admin/configuration');
    }

    async updateProviderConfig(providerType: string, config: unknown) {
        return this.request(`/admin/configuration/providers/${providerType}`, {
            method: 'PATCH',
            body: config,
        });
    }

    // Health Endpoint
    async getHealth() {
        return this.request<HealthStatus>('/health');
    }

    // Command Center Endpoints
    async getDashboardStats() {
        return this.request<DashboardStats>('/admin/dashboard/stats');
    }

    async getSystemHealth() {
        return this.request<SystemHealthResponse>('/admin/system-health');
    }

    async pauseAllCalls() {
        return this.request<PauseCallsResponse>('/admin/calls/pause', {
            method: 'POST',
        });
    }

    async setPauseAllCalls(paused: boolean, reason?: string) {
        return this.request<PauseCallsResponse>('/admin/calls/pause', {
            method: 'PUT',
            body: { paused, reason: reason?.trim() || undefined },
        });
    }

    async getPauseStatus() {
        return this.request<PauseCallsResponse>('/admin/calls/pause-status');
    }

    // Tenant Management Endpoints (Day 3)
    async getContactEnquiries(status: '' | 'new' | 'handled', offset = 0) {
        const params = new URLSearchParams({ limit: '50', offset: String(offset) });
        if (status) params.set('status', status);
        return this.request<ContactEnquiriesResponse>(`/admin/contact-enquiries?${params}`, { timeoutMs: 15_000 });
    }

    async getContactEnquiry(id: string) {
        const params = new URLSearchParams({ enquiry_id: id, limit: '1', offset: '0' });
        return this.request<ContactEnquiriesResponse>(`/admin/contact-enquiries?${params}`, { timeoutMs: 15_000 });
    }

    async updateContactEnquiry(id: string, status: 'new' | 'handled', expectedRevision: number) {
        return this.request<ContactEnquiry>(`/admin/contact-enquiries/${encodeURIComponent(id)}`, {
            method: 'PATCH', body: { status, expected_revision: expectedRevision }, timeoutMs: 15_000,
        });
    }

    async getFormalHoldStatus(target: HoldTarget) {
        return this.request<FormalHoldStatus>(`/admin/suspensions/${target.type}s/${encodeURIComponent(target.id)}/status`);
    }

    async getFormalHoldPropagation(eventId: string) {
        return this.request<HoldPropagation>(`/admin/suspensions/propagation-status/${encodeURIComponent(eventId)}`);
    }

    async createFormalHold(target: HoldTarget, body: HoldRequest, immediate: boolean) {
        return this.request<HoldReceipt>(`/admin/suspensions/${target.type}s/${encodeURIComponent(target.id)}/suspend${target.type === 'tenant' ? `?immediate=${immediate}` : ''}`, { method: 'POST', body });
    }

    async restoreFormalHold(target: HoldTarget, eventId: string, reason: string) {
        return this.request<HoldReceipt>(`/admin/suspensions/${target.type}s/${encodeURIComponent(target.id)}/restore`, {
            method: 'POST', body: { expected_suspension_id: eventId, reason },
        });
    }

    async submitFormalHoldAppeal(eventId: string, appealReason: string) {
        return this.request<HoldReceipt>(`/admin/suspensions/${encodeURIComponent(eventId)}/appeal`, { method: 'POST', body: { appeal_reason: appealReason } });
    }

    async reviewFormalHoldAppeal(eventId: string, decision: 'granted' | 'denied', response: string) {
        return this.request<HoldReceipt>(`/admin/suspensions/${encodeURIComponent(eventId)}/appeal/review`, { method: 'POST', body: { decision, response } });
    }

    async getTenants(search?: string, status?: string, limit?: number, offset?: number, includeArchived = false, sortBy?: 'name' | 'minutes_used') {
        const params = new URLSearchParams();
        if (search) params.append('search', search);
        if (status) params.append('status', status);
        if (limit !== undefined) params.append('limit', String(limit));
        if (offset !== undefined) params.append('offset', String(offset));
        if (includeArchived) params.append('include_archived', 'true');
        if (sortBy) params.append('sort_by', sortBy);
        const query = params.toString() ? '?' + params.toString() : '';
        return this.request<TenantListItem[]>(`/admin/tenants${query}`);
    }

    async getTenantById(tenantId: string) {
        return this.request<TenantDetails>(`/admin/tenants/${tenantId}`);
    }

    async suspendTenant(tenantId: string, expectedUpdatedAt: string | null) {
        return this.request<TenantMutationReceipt>(`/admin/tenants/${tenantId}/suspend`, {
            method: 'POST',
            body: { expected_updated_at: expectedUpdatedAt },
        });
    }

    async resumeTenant(tenantId: string, expectedUpdatedAt: string | null) {
        return this.request<TenantMutationReceipt>(`/admin/tenants/${tenantId}/resume`, {
            method: 'POST',
            body: { expected_updated_at: expectedUpdatedAt },
        });
    }

    async updateTenantQuota(tenantId: string, quota: QuotaUpdateRequest) {
        return this.request<TenantMutationReceipt>(
            `/admin/tenants/${tenantId}/quota`,
            {
                method: 'PATCH',
                body: quota,
            }
        );
    }

    // Calls Module (Day 4)
    async getLiveCalls() {
        return this.request<LiveCallItem[]>('/admin/calls/live');
    }

    async getCallHistory(params?: CallHistoryParams) {
        const searchParams = new URLSearchParams();
        if (params?.page) searchParams.append('page', params.page.toString());
        if (params?.page_size) searchParams.append('page_size', params.page_size.toString());
        if (params?.search) searchParams.append('search', params.search);
        if (params?.status) searchParams.append('status', params.status);
        if (params?.tenant_id) searchParams.append('tenant_id', params.tenant_id);
        if (params?.from_date) searchParams.append('from_date', params.from_date);
        if (params?.to_date) searchParams.append('to_date', params.to_date);
        if (params?.direction) searchParams.append('direction', params.direction);
        const query = searchParams.toString() ? '?' + searchParams.toString() : '';
        return this.request<CallHistoryResponse>(`/admin/calls/history${query}`);
    }

    async getAdminCallDetail(callId: string) {
        return this.request<AdminCallDetail>(`/admin/calls/${callId}`);
    }

    async inspectAdminCRMDelivery(callId: string, provider: AdminCRMReceipt['provider']) {
        return this.request<AdminCRMInspection>(
            `/admin/calls/${encodeURIComponent(callId)}/crm-deliveries/${encodeURIComponent(provider)}/inspection`,
            { timeoutMs: 25000 },
        );
    }

    async terminateCall(callId: string) {
        return this.request<TerminateCallResponse>(
            `/admin/calls/${callId}/terminate`,
            { method: 'POST' }
        );
    }

    async getAdminRecordings(params?: AdminRecordingParams) {
        const query = new URLSearchParams();
        if (params?.page) query.set('page', String(params.page));
        if (params?.page_size) query.set('page_size', String(params.page_size));
        if (params?.search) query.set('search', params.search);
        if (params?.status) query.set('status', params.status);
        if (params?.tenant_id) query.set('tenant_id', params.tenant_id);
        if (params?.call_id) query.set('call_id', params.call_id);
        if (params?.from_date) query.set('from_date', params.from_date);
        if (params?.to_date) query.set('to_date', params.to_date);
        if (params?.direction) query.set('direction', params.direction);
        const suffix = query.size ? `?${query.toString()}` : '';
        return this.request<AdminRecordingListResponse>(`/admin/recordings${suffix}`);
    }

    async getAdminFeedback(params?: AdminFeedbackParams) {
        const query = new URLSearchParams();
        if (params?.page) query.set('page', String(params.page));
        if (params?.page_size) query.set('page_size', String(params.page_size));
        if (params?.search) query.set('search', params.search);
        if (params?.transcript_status) query.set('transcript_status', params.transcript_status);
        if (params?.tenant_id) query.set('tenant_id', params.tenant_id);
        if (params?.call_id) query.set('call_id', params.call_id);
        if (params?.from_date) query.set('from_date', params.from_date);
        if (params?.to_date) query.set('to_date', params.to_date);
        if (params?.direction) query.set('direction', params.direction);
        const suffix = query.size ? `?${query.toString()}` : '';
        return this.request<AdminFeedbackListResponse>(`/admin/feedback${suffix}`);
    }

    async retryAdminFeedbackTranscription(feedbackId: string) {
        return this.request<AdminFeedbackItem>(
            `/admin/feedback/${feedbackId}/transcription/retry`,
            { method: 'POST' },
        );
    }

    async deleteAdminRecording(recordingId: string, reason: string, idempotencyKey: string) {
        return this.request<AdminMediaDeletionReceipt>(`/admin/recordings/${recordingId}`, {
            method: 'DELETE',
            body: { reason },
            headers: { 'Idempotency-Key': idempotencyKey },
        });
    }

    async deleteAdminFeedback(feedbackId: string, reason: string, idempotencyKey: string) {
        return this.request<AdminMediaDeletionReceipt>(`/admin/feedback/${feedbackId}`, {
            method: 'DELETE',
            body: { reason },
            headers: { 'Idempotency-Key': idempotencyKey },
        });
    }

    getAdminMediaDeletions(params: AdminMediaDeletionParams = {}) {
        const query = new URLSearchParams();
        for (const [key, value] of Object.entries(params)) {
            if (value !== undefined && value !== '') query.set(key, String(value));
        }
        return this.request<AdminMediaDeletionList>(`/admin/media-deletions${query.size ? `?${query}` : ''}`);
    }

    getAdminMediaDeletion(intentId: string) {
        return this.request<AdminMediaDeletionItem>(`/admin/media-deletions/${encodeURIComponent(intentId)}`);
    }

    getAdminRecordingAudio(recordingId: string) {
        return this.requestBlob(`/admin/recordings/${recordingId}/audio`);
    }

    getAdminFeedbackAudio(feedbackId: string) {
        return this.requestBlob(`/admin/feedback/${feedbackId}/audio`);
    }

    // Inbound operations. Every mutation is versioned and carries an
    // idempotency key so a repeated click or network retry is safe.
    async getAdminInboundOverview() {
        return this.request<AdminInboundOverview>('/admin/inbound/overview');
    }

    async getAdminInboundAssignments(params?: AdminInboundAssignmentParams) {
        const query = new URLSearchParams();
        if (params?.tenant_id) query.set('tenant_id', params.tenant_id);
        if (params?.status) query.set('status', params.status);
        if (params?.search) query.set('search', params.search);
        const suffix = query.size ? `?${query.toString()}` : '';
        return this.request<AdminInboundAssignmentListResponse>(
            `/admin/inbound/assignments${suffix}`,
        );
    }

    async getAdminInboundControls() {
        return this.request<AdminInboundRuntimeControls>('/admin/inbound/controls');
    }

    async updateAdminInboundControls(
        payload: Partial<Pick<
            AdminInboundRuntimeControls,
            'inbound_enabled' | 'recording_enabled' | 'transfer_enabled' | 'settlement_enabled'
        >> & { reason: string; expected_version: number },
        idempotencyKey: string,
    ) {
        return this.request<AdminInboundRuntimeControls>('/admin/inbound/controls', {
            method: 'PATCH',
            body: payload,
            headers: { 'Idempotency-Key': idempotencyKey },
        });
    }

    async quarantineAdminInboundAssignment(
        assignmentId: string,
        expectedVersion: number,
        reason: string,
        idempotencyKey: string,
    ) {
        return this.request<Pick<AdminInboundAssignment, 'id' | 'status' | 'version' | 'masked_did'> & { quarantine_reason?: string | null }>(
            `/admin/inbound/assignments/${assignmentId}/quarantine`,
            {
                method: 'POST',
                body: { expected_version: expectedVersion, reason },
                headers: { 'Idempotency-Key': idempotencyKey },
            },
        );
    }

    async unquarantineAdminInboundAssignment(
        assignmentId: string,
        expectedVersion: number,
        reason: string,
        idempotencyKey: string,
    ) {
        return this.request<Pick<AdminInboundAssignment, 'id' | 'status' | 'version' | 'masked_did'> & { quarantine_reason?: string | null }>(
            `/admin/inbound/assignments/${assignmentId}/unquarantine`,
            {
                method: 'POST',
                body: { expected_version: expectedVersion, reason },
                headers: { 'Idempotency-Key': idempotencyKey },
            },
        );
    }

    async requestAdminInboundReassignment(
        payload: {
            assignment_id: string;
            target_tenant_id: string;
            target_campaign_id: string;
            reason: string;
            expected_version: number;
        },
        idempotencyKey: string,
    ) {
        return this.request<AdminInboundReassignmentRequest>('/admin/inbound/reassignments', {
            method: 'POST',
            body: payload,
            headers: { 'Idempotency-Key': idempotencyKey },
        });
    }

    async getAdminInboundReassignments(status = 'pending', limit = 50, cursor?: string) {
        const query = new URLSearchParams({ status, limit: String(limit) });
        if (cursor) query.set('cursor', cursor);
        return this.request<AdminInboundReassignmentListResponse>(
            `/admin/inbound/reassignments?${query.toString()}`,
        );
    }

    async getAdminInboundReassignment(requestId: string) {
        return this.request<AdminInboundReassignmentRequest>(`/admin/inbound/reassignments/${encodeURIComponent(requestId)}`);
    }

    async approveAdminInboundReassignment(
        requestId: string,
        reason: string,
        idempotencyKey: string,
    ) {
        return this.request<AdminInboundReassignmentRequest>(
            `/admin/inbound/reassignments/${requestId}/approve`,
            {
                method: 'POST',
                body: { reason },
                headers: { 'Idempotency-Key': idempotencyKey },
            },
        );
    }

    async cancelAdminInboundReassignment(requestId: string, reason: string, idempotencyKey: string) {
        return this.request<AdminInboundReassignmentRequest>(
            `/admin/inbound/reassignments/${encodeURIComponent(requestId)}/cancel`,
            { method: 'POST', body: { reason }, headers: { 'Idempotency-Key': idempotencyKey } },
        );
    }

    // =========================================================================
    // Day 5: Actions API
    // =========================================================================

    async getActions(params?: ActionListParams) {
        const searchParams = new URLSearchParams();
        if (params?.page) searchParams.append('page', params.page.toString());
        if (params?.page_size) searchParams.append('page_size', params.page_size.toString());
        if (params?.search) searchParams.append('search', params.search);
        if (params?.status) searchParams.append('status', params.status);
        if (params?.type) searchParams.append('type', params.type);
        if (params?.tenant_id) searchParams.append('tenant_id', params.tenant_id);
        if (params?.from_date) searchParams.append('from', params.from_date);
        if (params?.to_date) searchParams.append('to', params.to_date);
        const query = searchParams.toString() ? '?' + searchParams.toString() : '';
        return this.request<ActionListResponse>(`/admin/actions${query}`);
    }

    async getActionDetail(actionId: string) {
        return this.request<ActionDetail>(`/admin/actions/${actionId}`);
    }

    async recoverSavedAcknowledgement(actionId: string, body: AcknowledgementRecoveryRequest) {
        return this.request<AcknowledgementRecoveryRecord>(`/admin/actions/${encodeURIComponent(actionId)}/recover-acknowledgement`,
            { method: 'POST', body, timeoutMs: 25000 });
    }

    async retryAction(actionId: string) {
        return this.request<{ detail: string; original_action_id: string; new_action_id: string; status: string }>(
            `/admin/actions/${actionId}/retry`,
            { method: 'POST' }
        );
    }

    async inspectAdminEmailAction(actionId: string) {
        return this.request<EmailInspection>(`/admin/actions/${encodeURIComponent(actionId)}/email-inspection`,
            { timeoutMs: 25000 });
    }

    async inspectAdminCalendarAction(actionId: string) {
        return this.request<CalendarInspection>(`/admin/actions/${encodeURIComponent(actionId)}/calendar-inspection`,
            { timeoutMs: 25000 });
    }

    async cancelAction(actionId: string) {
        return this.request<{ detail: string; action_id: string; new_status: string }>(
            `/admin/actions/${actionId}/cancel`,
            { method: 'POST' }
        );
    }

    // =========================================================================
    // Day 6: Connectors API
    // =========================================================================

    async getConnectors(params?: ConnectorListParams) {
        const searchParams = new URLSearchParams();
        if (params?.page) searchParams.append('page', params.page.toString());
        if (params?.page_size) searchParams.append('page_size', params.page_size.toString());
        if (params?.tenant_id) searchParams.append('tenant_id', params.tenant_id);
        if (params?.status) searchParams.append('status', params.status);
        if (params?.type) searchParams.append('type', params.type);
        if (params?.provider) searchParams.append('provider', params.provider);
        const query = searchParams.toString() ? '?' + searchParams.toString() : '';
        return this.request<AdminConnectorListResponse>(`/admin/connectors${query}`);
    }

    async getConnectorDetail(connectorId: string) {
        return this.request<AdminConnectorDetail>(`/admin/connectors/${connectorId}`);
    }

    async forceReconnect(connectorId: string) {
        return this.request<{ success: boolean; message: string; connector_id: string; refreshed_at: string }>(
            `/admin/connectors/${connectorId}/reconnect`,
            { method: 'POST' }
        );
    }

    async revokeConnector(connectorId: string) {
        return this.request<{ success: boolean; message: string; connector_id: string;
            provider_revoked?: boolean; provider_revocation_status?: 'not_requested' | 'pending' | 'confirmed' | 'unconfirmed' }>(
            `/admin/connectors/${connectorId}/revoke`,
            { method: 'POST' }
        );
    }

    // =========================================================================
    // Day 6: Usage API
    // =========================================================================

    async getUsageSummary(params?: UsageParams) {
        const searchParams = new URLSearchParams();
        if (params?.tenant_id) searchParams.append('tenant_id', params.tenant_id);
        if (params?.from_date) searchParams.append('from_date', params.from_date);
        if (params?.to_date) searchParams.append('to_date', params.to_date);
        const query = searchParams.toString() ? '?' + searchParams.toString() : '';
        return this.request<UsageSummaryResponse>(`/admin/usage/summary${query}`);
    }

    async getUsageBreakdown(params?: UsageParams) {
        const searchParams = new URLSearchParams();
        if (params?.tenant_id) searchParams.append('tenant_id', params.tenant_id);
        if (params?.from_date) searchParams.append('from_date', params.from_date);
        if (params?.to_date) searchParams.append('to_date', params.to_date);
        if (params?.group_by) searchParams.append('group_by', params.group_by);
        const query = searchParams.toString() ? '?' + searchParams.toString() : '';
        return this.request<UsageBreakdownResponse>(`/admin/usage/breakdown${query}`);
    }

    async getSupplierStatements(fromDate: string, toDate: string, offset = 0) {
        const query = new URLSearchParams({ from_date: fromDate, to_date: toDate, offset: String(offset) });
        return this.request<unknown>(`/admin/usage/statements?${query}`);
    }

    async importSupplierStatement(body: Record<string, unknown>) {
        return this.request<unknown>('/admin/usage/statements', { method: 'POST', body });
    }

    async getSupplierImport(requestId: string) {
        return this.request<unknown>(`/admin/usage/statements/imports/${encodeURIComponent(requestId)}`);
    }

    async getSupplierStatement(statementId: string) {
        return this.request<unknown>(`/admin/usage/statements/${encodeURIComponent(statementId)}`);
    }

    async reviewSupplierStatement(statementId: string, body: Record<string, unknown>) {
        return this.request<unknown>(`/admin/usage/statements/${encodeURIComponent(statementId)}/reviews`, { method: 'POST', body });
    }

    async getSupplierSource(statementId: string) {
        return this.requestBlob(`/admin/usage/statements/${encodeURIComponent(statementId)}/source`);
    }

    // =========================================================================
    // Day 8: Enhanced System Health API
    // =========================================================================

    async getDetailedHealth() {
        return this.request<DetailedHealthResponse>('/admin/health/detailed');
    }

    async getWorkers() {
        return this.request<WorkersResponse>('/admin/health/workers');
    }

    async getQueues() {
        return this.request<QueuesResponse>('/admin/health/queues');
    }

    async getDatabaseHealth() {
        return this.request<DatabaseHealthResponse>('/admin/health/database');
    }

    async getIncidents(params?: IncidentsParams) {
        const searchParams = new URLSearchParams();
        if (params?.page) searchParams.append('page', params.page.toString());
        if (params?.page_size) searchParams.append('page_size', params.page_size.toString());
        if (params?.status) searchParams.append('status', params.status);
        if (params?.severity) searchParams.append('severity', params.severity);
        const query = searchParams.toString() ? '?' + searchParams.toString() : '';
        return this.request<IncidentsResponse>(`/admin/incidents${query}`);
    }

    async getIncident(incidentId: string) {
        return this.request<IncidentItem>(`/admin/incidents/${incidentId}`);
    }

    async acknowledgeIncident(incidentId: string) {
        return this.request<IncidentTransitionReceipt>(
            `/admin/incidents/${incidentId}/acknowledge`,
            { method: 'POST' }
        );
    }

    async resolveIncident(incidentId: string) {
        return this.request<IncidentTransitionReceipt>(
            `/admin/incidents/${incidentId}/resolve`,
            { method: 'POST' }
        );
    }

    async getAlertSettings() {
        return this.request<AlertSettings>('/admin/alerts/settings');
    }

    // =============================================================================
    // Passkey / WebAuthn Methods
    // =============================================================================

    async checkUserHasPasskeys(email: string): Promise<boolean> {
        try {
            const response = await this.request<{ has_passkeys: boolean }>('/auth/passkey-check', {
                method: 'POST',
                body: { email },
            });
            return response.data?.has_passkeys ?? false;
        } catch {
            return false;
        }
    }

    async beginPasskeyRegistration(
        authenticatorType: 'platform' | 'cross-platform' | 'any' = 'any',
        displayName?: string
    ): Promise<{ ceremony_id: string; options: Record<string, unknown> }> {
        const response = await this.request<{
            ceremony_id: string;
            options: Record<string, unknown>;
        }>('/auth/passkeys/register/begin', {
            method: 'POST',
            body: {
                authenticator_type: authenticatorType,
                display_name: displayName,
            },
        });
        if (response.error || !response.data) {
            throw new Error(response.error?.message || 'Could not begin passkey registration');
        }
        return response.data;
    }

    async completePasskeyRegistration(
        ceremonyId: string,
        credentialResponse: Record<string, unknown>,
        displayName?: string
    ): Promise<{ passkey_id: string; message: string }> {
        const response = await this.request<{ passkey_id: string; message: string }>(
            '/auth/passkeys/register/complete', {
            method: 'POST',
            body: {
                ceremony_id: ceremonyId,
                credential_response: credentialResponse,
                display_name: displayName,
            },
        });
        if (response.error || !response.data) {
            throw new Error(response.error?.message || 'Could not complete passkey registration');
        }
        return response.data;
    }

    async beginPasskeyLogin(email?: string): Promise<{
        ceremony_id: string;
        options: Record<string, unknown>;
        has_passkeys: boolean;
    }> {
        const response = await this.request<{
            ceremony_id: string;
            options: Record<string, unknown>;
            has_passkeys: boolean;
        }>('/auth/passkeys/login/begin', {
            method: 'POST',
            body: { email },
        });
        if (response.error || !response.data) {
            throw new Error(response.error?.message || 'Could not begin passkey login');
        }
        return response.data;
    }

    async completePasskeyLogin(
        ceremonyId: string,
        credentialResponse: Record<string, unknown>
    ): Promise<AuthResponse> {
        const response = await this.request<AuthResponse>('/auth/passkeys/login/complete', {
            method: 'POST',
            body: {
                ceremony_id: ceremonyId,
                credential_response: credentialResponse,
            },
        });
        if (response.error || !response.data) {
            throw new Error(response.error?.message || 'Could not complete passkey login');
        }
        return response.data;
    }

    async listPasskeys(): Promise<Array<{
        id: string;
        credential_id: string;
        display_name: string;
        device_type: string;
        backed_up: boolean;
        transports: string[];
        created_at: string;
        last_used_at?: string;
    }>> {
        const response = await this.request<{ passkeys: Array<{
            id: string;
            credential_id: string;
            display_name: string;
            device_type: string;
            backed_up: boolean;
            transports: string[];
            created_at: string;
            last_used_at?: string;
        }> }>('/auth/passkeys');
        if (response.error || !response.data) {
            throw new Error(response.error?.message || 'Could not list passkeys');
        }
        return response.data.passkeys;
    }

    async updatePasskey(passkeyId: string, displayName: string): Promise<void> {
        const response = await this.request(`/auth/passkeys/${passkeyId}`, {
            method: 'PATCH',
            body: { display_name: displayName },
        });
        if (response.error) throw new Error(response.error.message);
    }

    async deletePasskey(passkeyId: string): Promise<void> {
        const response = await this.request(`/auth/passkeys/${passkeyId}`, {
            method: 'DELETE',
        });
        if (response.error) throw new Error(response.error.message);
    }
}

// Export singleton instance
export const api = new ApiClient(API_BASE_URL);
export default api;
