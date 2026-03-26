/**
 * API Client for Admin Panel
 * Aligned with Talky.ai Backend API
 */

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1';

// Types - Define before usage
export interface AdminUser {
    id: string;
    email: string;
    name: string;
    role: 'admin' | 'super_admin';
    tenant_id?: string;
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
}

export interface QuotaUpdateRequest {
    minutes_allocated?: number;
    max_concurrent_calls?: number;
}

// =============================================================================
// Day 4: Calls Module Types
// =============================================================================

export interface LiveCallItem {
    id: string;
    tenant_id: string;
    tenant_name: string;
    phone_number: string;
    campaign_name: string | null;
    status: 'in_progress' | 'ringing' | 'queued' | 'initiated';
    started_at: string | null;
    duration_seconds: number;
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
    recording_url: string | null;
    cost: number | null;
    timeline: TimelineEvent[];
    created_at: string;
    updated_at?: string;
}

export interface CallHistoryParams {
    page?: number;
    page_size?: number;
    search?: string;
    status?: string;
    tenant_id?: string;
    from_date?: string;
    to_date?: string;
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
}

export interface ConnectorListParams {
    tenant_id?: string;
    status?: string;
    type?: string;
    provider?: string;
    page?: number;
    page_size?: number;
}

export interface UsageBreakdownItem {
    provider: string;
    usage_type: string;
    total_units: number;
    estimated_cost: number;
    tenant_count: number;
}

export interface UsageSummaryResponse {
    total_cost: number;
    total_call_minutes: number;
    total_api_calls: number;
    providers: UsageBreakdownItem[];
    period_start: string;
    period_end: string;
}

export interface UsageBreakdownResponse {
    breakdown: Record<string, unknown>[];
    group_by: string;
    period_start: string;
    period_end: string;
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
    error_rate_24h: string;
    active_tenants: number;
    api_errors_24h: number;
}

export interface SystemHealthItem {
    name: string;
    status: 'operational' | 'degraded' | 'down';
    latency_ms: number;
    latency_display: string;
}

export interface SystemHealthResponse {
    providers: SystemHealthItem[];
}

export interface PauseCallsResponse {
    paused: boolean;
    paused_at: string | null;
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
}

export interface WorkerStatus {
    id: string;
    name: string;
    status: 'idle' | 'busy' | 'offline';
    current_task: string | null;
    processed_count: number;
    success_rate: number;
    uptime_seconds: number;
    last_heartbeat: string;
}

export interface WorkersResponse {
    workers: WorkerStatus[];
    total_workers: number;
    active_workers: number;
    busy_workers: number;
}

export interface QueueStatus {
    name: string;
    pending: number;
    processing: number;
    failed: number;
    completed_24h: number;
    avg_processing_time_ms: number;
    success_rate_24h: number;
}

export interface QueuesResponse {
    queues: QueueStatus[];
    total_pending: number;
    total_processing: number;
}

export interface DatabaseHealthResponse {
    status: string;
    latency_ms: number;
    connections_active: number;
    connections_max: number;
    pool_utilization_percent: number;
    checked_at: string;
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
}

export interface IncidentsResponse {
    items: IncidentItem[];
    total: number;
    page: number;
    page_size: number;
}

export interface IncidentsParams {
    page?: number;
    page_size?: number;
    status?: 'open' | 'acknowledged' | 'resolved';
    severity?: 'critical' | 'warning' | 'info';
}

export interface AlertSettings {
    error_rate_threshold: number;
    latency_threshold_ms: number;
    queue_depth_threshold: number;
    memory_threshold_percent: number;
    cpu_threshold_percent: number;
    email_notifications: boolean;
    slack_notifications: boolean;
    slack_webhook_url: string | null;
}

// API Response wrapper
interface ApiResponse<T> {
    data?: T;
    error?: {
        code: string;
        message: string;
        details?: unknown;
    };
}

interface RequestOptions {
    method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
    body?: unknown;
    headers?: Record<string, string>;
}

// API Client Class
class ApiClient {
    private baseUrl: string;
    private token: string | null = null;

    constructor(baseUrl: string) {
        this.baseUrl = baseUrl;
        // Try to get token from localStorage on init
        if (typeof window !== 'undefined') {
            this.token = localStorage.getItem('admin_token');
        }
    }

    setToken(token: string | null) {
        this.token = token;
        if (token) {
            localStorage.setItem('admin_token', token);
        } else {
            localStorage.removeItem('admin_token');
        }
    }

    getToken(): string | null {
        return this.token;
    }

    private async request<T>(endpoint: string, options: RequestOptions = {}): Promise<ApiResponse<T>> {
        const { method = 'GET', body, headers = {} } = options;

        const requestHeaders: Record<string, string> = {
            'Content-Type': 'application/json',
            ...headers,
        };

        if (this.token) {
            requestHeaders['Authorization'] = `Bearer ${this.token}`;
        }

        try {
            const response = await fetch(`${this.baseUrl}${endpoint}`, {
                method,
                headers: requestHeaders,
                body: body ? JSON.stringify(body) : undefined,
            });

            const data = await response.json();

            if (!response.ok) {
                return {
                    error: {
                        code: data.error?.code || 'UNKNOWN_ERROR',
                        message: data.error?.message || data.detail || 'An error occurred',
                        details: data.error?.details,
                    },
                };
            }

            return { data };
        } catch (error) {
            return {
                error: {
                    code: 'NETWORK_ERROR',
                    message: error instanceof Error ? error.message : 'Network error occurred',
                },
            };
        }
    }

    // Auth Endpoints
    async login(email: string, password: string) {
        return this.request<{ access_token: string; user: AdminUser }>('/auth/login', {
            method: 'POST',
            body: { email, password },
        });
    }

    async verifyToken() {
        return this.request<{ valid: boolean; user: AdminUser }>('/auth/verify');
    }

    async logout() {
        return this.request('/auth/logout', { method: 'POST' });
    }

    // Admin Endpoints
    // Note: getTenants, getTenantById, updateTenantQuota, suspendTenant, resumeTenant
    // are defined below in the "Tenant Management Endpoints (Day 3)" section

    async getUsers(params?: UserQueryParams) {
        const query = params ? '?' + new URLSearchParams(params as Record<string, string>).toString() : '';
        return this.request<UsersResponse>(`/admin/users${query}`);
    }

    async getUser(userId: string) {
        return this.request<UserDetails>(`/admin/users/${userId}`);
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
    async getAuditLog(params?: AuditQueryParams) {
        const query = params ? '?' + new URLSearchParams(params as Record<string, string>).toString() : '';
        return this.request<AuditResponse>(`/admin/audit${query}`);
    }

    async getSecurityEvents(params?: SecurityQueryParams) {
        const query = params ? '?' + new URLSearchParams(params as Record<string, string>).toString() : '';
        return this.request<SecurityEventsResponse>(`/admin/security/events${query}`);
    }

    // Configuration Endpoints
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

    async getPauseStatus() {
        return this.request<PauseCallsResponse>('/admin/calls/pause-status');
    }

    // Tenant Management Endpoints (Day 3)
    async getTenants(search?: string, status?: string) {
        const params = new URLSearchParams();
        if (search) params.append('search', search);
        if (status) params.append('status', status);
        const query = params.toString() ? '?' + params.toString() : '';
        return this.request<TenantListItem[]>(`/admin/tenants${query}`);
    }

    async getTenantById(tenantId: string) {
        return this.request<TenantDetails>(`/admin/tenants/${tenantId}`);
    }

    async suspendTenant(tenantId: string) {
        return this.request<{ detail: string; status: string }>(`/admin/tenants/${tenantId}/suspend`, {
            method: 'POST',
        });
    }

    async resumeTenant(tenantId: string) {
        return this.request<{ detail: string; status: string }>(`/admin/tenants/${tenantId}/resume`, {
            method: 'POST',
        });
    }

    async updateTenantQuota(tenantId: string, quota: QuotaUpdateRequest) {
        return this.request<{ detail: string; minutes_allocated?: number; max_concurrent_calls?: number }>(
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
        const query = searchParams.toString() ? '?' + searchParams.toString() : '';
        return this.request<CallHistoryResponse>(`/admin/calls/history${query}`);
    }

    async getAdminCallDetail(callId: string) {
        return this.request<AdminCallDetail>(`/admin/calls/${callId}`);
    }

    async terminateCall(callId: string) {
        return this.request<{ detail: string; call_id: string; new_status: string }>(
            `/admin/calls/${callId}/terminate`,
            { method: 'POST' }
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

    async retryAction(actionId: string) {
        return this.request<{ detail: string; original_action_id: string; new_action_id: string; status: string }>(
            `/admin/actions/${actionId}/retry`,
            { method: 'POST' }
        );
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
        return this.request<{ success: boolean; message: string; connector_id: string; revoked_at: string }>(
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

    async acknowledgeIncident(incidentId: string) {
        return this.request<{ success: boolean; message: string; incident_id: string }>(
            `/admin/incidents/${incidentId}/acknowledge`,
            { method: 'POST' }
        );
    }

    async resolveIncident(incidentId: string) {
        return this.request<{ success: boolean; message: string; incident_id: string }>(
            `/admin/incidents/${incidentId}/resolve`,
            { method: 'POST' }
        );
    }

    async getAlertSettings() {
        return this.request<AlertSettings>('/admin/alerts/settings');
    }

    async updateAlertSettings(settings: Partial<AlertSettings>) {
        return this.request<AlertSettings>('/admin/alerts/settings', {
            method: 'PUT',
            body: settings
        });
    }
}

    // =============================================================================
    // Passkey / WebAuthn Methods
    // =============================================================================

    async checkUserHasPasskeys(email: string): Promise<boolean> {
        try {
            const response = await this.request<{ has_passkeys: boolean }>('/auth/passkey-check', {
                method: 'POST',
                body: JSON.stringify({ email }),
            });
            return response.has_passkeys;
        } catch {
            return false;
        }
    }

    async beginPasskeyRegistration(
        authenticatorType: 'platform' | 'cross-platform' | 'any' = 'any',
        displayName?: string
    ): Promise<{ ceremony_id: string; options: Record<string, unknown> }> {
        return this.request('/auth/passkeys/register/begin', {
            method: 'POST',
            body: JSON.stringify({
                authenticator_type: authenticatorType,
                display_name: displayName,
            }),
        });
    }

    async completePasskeyRegistration(
        ceremonyId: string,
        credentialResponse: Record<string, unknown>,
        displayName?: string
    ): Promise<{ passkey_id: string; message: string }> {
        return this.request('/auth/passkeys/register/complete', {
            method: 'POST',
            body: JSON.stringify({
                ceremony_id: ceremonyId,
                credential_response: credentialResponse,
                display_name: displayName,
            }),
        });
    }

    async beginPasskeyLogin(email?: string): Promise<{
        ceremony_id: string;
        options: Record<string, unknown>;
        has_passkeys: boolean;
    }> {
        return this.request('/auth/passkeys/login/begin', {
            method: 'POST',
            body: JSON.stringify({ email }),
        });
    }

    async completePasskeyLogin(
        ceremonyId: string,
        credentialResponse: Record<string, unknown>
    ): Promise<AuthResponse> {
        return this.request('/auth/passkeys/login/complete', {
            method: 'POST',
            body: JSON.stringify({
                ceremony_id: ceremonyId,
                credential_response: credentialResponse,
            }),
        });
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
        return response.passkeys;
    }

    async updatePasskey(passkeyId: string, displayName: string): Promise<void> {
        await this.request(`/auth/passkeys/${passkeyId}`, {
            method: 'PATCH',
            body: JSON.stringify({ display_name: displayName }),
        });
    }

    async deletePasskey(passkeyId: string): Promise<void> {
        await this.request(`/auth/passkeys/${passkeyId}`, {
            method: 'DELETE',
        });
    }
}

// Export singleton instance
export const api = new ApiClient(API_BASE_URL);
export default api;
