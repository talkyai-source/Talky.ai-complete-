import { createHttpClient } from "@/lib/http-client";
import { backendEndpoints } from "@/lib/backend-endpoints";
import {
    AssistantActionSchema,
    AssistantPlanSchema,
    AssistantRunSchema,
    AuditLogEventSchema,
    ConnectorResponseSchema,
    ConnectorAccountSchema,
    ConnectorProviderStatusSchema,
    EmailSendResponseSchema,
    EmailTemplateResponseSchema,
    ListResponseSchema,
    PaginatedResponseSchema,
    CalendarEventResponseSchema,
    MeetingResponseSchema,
    ReminderSchema,
    SecurityEventSchema,
    PartnerSummarySchema,
    TenantSummarySchema,
    VoiceCallGuardResponseSchema,
    VoiceCallStartResponseSchema,
    type AssistantAction,
    type AssistantPlan,
    type AssistantRun,
    type AuditLogEvent,
    type CalendarEvent,
    type Connector,
    type ConnectorAccount,
    type ConnectorProviderStatus,
    type EmailSendResponse,
    type EmailTemplate,
    type ListResponse,
    type Meeting,
    type PartnerSummary,
    type Reminder,
    type ReminderChannel,
    type ReminderStatus,
    type SecurityEvent,
    type TenantSummary,
    type VoiceCallGuardResponse,
    type VoiceCallStartResponse,
    type VoiceFeature,
} from "@/lib/models";
import { extractAuthorizationUrl } from "@/lib/connectors-utils";
import { apiBaseUrl } from "@/lib/env";

let _httpClient: ReturnType<typeof createHttpClient> | undefined;

function httpClient() {
    if (_httpClient) return _httpClient;
    _httpClient = createHttpClient({ baseUrl: apiBaseUrl(), getToken: () => null, setToken: () => {} });
    return _httpClient;
}

function parseOrThrow<T>(schema: { parse: (v: unknown) => T }, data: unknown) {
    return schema.parse(data);
}

export type AuditLogsListInput = {
    page?: number;
    pageSize?: number;
    eventType?: string;
    from?: string;
    to?: string;
    userQuery?: string;
    tenantId?: string;
    partnerId?: string;
};

export type SecurityEventsListInput = {
    page?: number;
    pageSize?: number;
    eventType?: string;
    severity?: "low" | "medium" | "high";
    from?: string;
    to?: string;
    userQuery?: string;
    tenantId?: string;
    partnerId?: string;
};

export type AdminResourceListInput = {
    page?: number;
    pageSize?: number;
    query?: string;
    status?: "active" | "suspended";
    partnerId?: string;
};

export const backendApi = {
    health: async (signal?: AbortSignal) => {
        const data = await httpClient().request<{ status: string }>({ path: backendEndpoints.health.path, timeoutMs: 2500, signal });
        return data;
    },
    connectors: {
        list: async (signal?: AbortSignal): Promise<ListResponse<Connector>> => {
            const data = await httpClient().request({ path: backendEndpoints.connectorsList.path, timeoutMs: 12_000, signal });
            return parseOrThrow(ListResponseSchema(ConnectorResponseSchema), data);
        },
        create: async (input: Pick<Connector, "name" | "type" | "config">): Promise<Connector> => {
            const data = await httpClient().request({
                path: backendEndpoints.connectorsCreate.path,
                method: backendEndpoints.connectorsCreate.method,
                body: input,
                timeoutMs: 12_000,
            });
            return parseOrThrow(ConnectorResponseSchema, data);
        },
        status: async (signal?: AbortSignal): Promise<ListResponse<ConnectorProviderStatus>> => {
            const data = await httpClient().request({ path: backendEndpoints.connectorsStatus.path, timeoutMs: 12_000, signal });
            return parseOrThrow(ListResponseSchema(ConnectorProviderStatusSchema), data);
        },
        authorize: async (input: { type: string; redirect_uri: string }): Promise<{ authorization_url: string }> => {
            const data = await httpClient().request({
                path: backendEndpoints.connectorsAuthorize.path.replace("{type}", encodeURIComponent(input.type)),
                method: backendEndpoints.connectorsAuthorize.method,
                query: { redirect_uri: input.redirect_uri },
                timeoutMs: 12_000,
            });
            return { authorization_url: extractAuthorizationUrl(data) };
        },
        disconnect: async (input: { type: string }): Promise<void> => {
            await httpClient().request({
                path: backendEndpoints.connectorsDisconnect.path.replace("{type}", encodeURIComponent(input.type)),
                method: backendEndpoints.connectorsDisconnect.method,
                timeoutMs: 12_000,
            });
        },
    },
    connectorAccounts: {
        list: async (connectorId?: string, signal?: AbortSignal): Promise<ListResponse<ConnectorAccount>> => {
            const data = await httpClient().request({
                path: backendEndpoints.connectorAccountsList.path,
                query: connectorId ? { connector_id: connectorId } : undefined,
                timeoutMs: 12_000,
                signal,
            });
            return parseOrThrow(ListResponseSchema(ConnectorAccountSchema), data);
        },
    },
    meetings: {
        list: async (signal?: AbortSignal): Promise<ListResponse<Meeting>> => {
            const data = await httpClient().request({ path: backendEndpoints.meetingsList.path, timeoutMs: 12_000, signal });
            return parseOrThrow(ListResponseSchema(MeetingResponseSchema), data);
        },
    },
    calendarEvents: {
        list: async (
            input?: { page?: number; pageSize?: number },
            signal?: AbortSignal
        ): Promise<{ items: CalendarEvent[]; total?: number; page?: number; page_size?: number }> => {
            const data = await httpClient().request({
                path: backendEndpoints.calendarEventsList.path,
                timeoutMs: 12_000,
                signal,
                query: {
                    page: input?.page,
                    page_size: input?.pageSize,
                },
            });
            return parseOrThrow(PaginatedResponseSchema(CalendarEventResponseSchema), data);
        },
        create: async (input: {
            leadId: string;
            leadName?: string;
            title: string;
            startTime: string;
            endTime?: string;
            notes?: string;
        }): Promise<CalendarEvent> => {
            const data = await httpClient().request({
                path: backendEndpoints.calendarEventsCreate.path,
                method: backendEndpoints.calendarEventsCreate.method,
                body: {
                    lead_id: input.leadId,
                    lead_name: input.leadName,
                    title: input.title,
                    start_time: input.startTime,
                    end_time: input.endTime,
                    notes: input.notes,
                },
                timeoutMs: 12_000,
            });
            return parseOrThrow(CalendarEventResponseSchema, data);
        },
        update: async (
            id: string,
            patch: {
                title?: string;
                startTime?: string;
                endTime?: string;
                status?: string;
                leadId?: string;
                leadName?: string;
                notes?: string;
                joinLink?: string;
                calendarLink?: string;
                participants?: Array<{ id?: string; name?: string; email?: string; role?: string }>;
            }
        ): Promise<CalendarEvent> => {
            const data = await httpClient().request({
                path: backendEndpoints.calendarEventsUpdate.path.replace("{id}", encodeURIComponent(id)),
                method: backendEndpoints.calendarEventsUpdate.method,
                body: {
                    ...(patch.title !== undefined ? { title: patch.title } : {}),
                    ...(patch.startTime !== undefined ? { start_time: patch.startTime } : {}),
                    ...(patch.endTime !== undefined ? { end_time: patch.endTime } : {}),
                    ...(patch.status !== undefined ? { status: patch.status } : {}),
                    ...(patch.leadId !== undefined ? { lead_id: patch.leadId } : {}),
                    ...(patch.leadName !== undefined ? { lead_name: patch.leadName } : {}),
                    ...(patch.notes !== undefined ? { notes: patch.notes } : {}),
                    ...(patch.joinLink !== undefined ? { join_link: patch.joinLink } : {}),
                    ...(patch.calendarLink !== undefined ? { calendar_link: patch.calendarLink } : {}),
                    ...(patch.participants !== undefined ? { participants: patch.participants } : {}),
                },
                timeoutMs: 12_000,
            });
            return parseOrThrow(CalendarEventResponseSchema, data);
        },
        cancel: async (id: string): Promise<void> => {
            await httpClient().request({
                path: backendEndpoints.calendarEventsDelete.path.replace("{id}", encodeURIComponent(id)),
                method: backendEndpoints.calendarEventsDelete.method,
                timeoutMs: 12_000,
            });
        },
    },
    reminders: {
        list: async (signal?: AbortSignal): Promise<ListResponse<Reminder>> => {
            const data = await httpClient().request({ path: backendEndpoints.remindersList.path, timeoutMs: 12_000, signal });
            return parseOrThrow(ListResponseSchema(ReminderSchema), data);
        },
        create: async (input: {
            content: string;
            channel: ReminderChannel;
            scheduledAt: string;
            meetingId?: string;
            meetingTitle?: string;
            contactId?: string;
            contactName?: string;
            toEmail?: string;
            toPhone?: string;
        }): Promise<Reminder> => {
            const data = await httpClient().request({
                path: backendEndpoints.remindersCreate.path,
                method: backendEndpoints.remindersCreate.method,
                body: {
                    content: input.content,
                    channel: input.channel,
                    scheduled_at: input.scheduledAt,
                    meeting_id: input.meetingId,
                    meeting_title: input.meetingTitle,
                    contact_id: input.contactId,
                    contact_name: input.contactName,
                    to_email: input.toEmail,
                    to_phone: input.toPhone,
                },
                timeoutMs: 12_000,
            });
            return parseOrThrow(ReminderSchema, data);
        },
        update: async (
            id: string,
            patch:
                | { content?: string; status?: ReminderStatus; channel?: ReminderChannel; scheduledAt?: string }
                | { content?: string; due_date?: string; is_completed?: boolean }
        ): Promise<Reminder> => {
            const data = await httpClient().request({
                path: backendEndpoints.remindersUpdate.path.replace("{id}", encodeURIComponent(id)),
                method: backendEndpoints.remindersUpdate.method,
                body:
                    "scheduledAt" in patch
                        ? {
                              content: patch.content,
                              status: patch.status,
                              channel: patch.channel,
                              scheduled_at: patch.scheduledAt,
                          }
                        : patch,
                timeoutMs: 12_000,
            });
            return parseOrThrow(ReminderSchema, data);
        },
        cancel: async (id: string): Promise<Reminder> => {
            const data = await httpClient().request({
                path: backendEndpoints.remindersCancel.path.replace("{id}", encodeURIComponent(id)),
                method: backendEndpoints.remindersCancel.method,
                timeoutMs: 12_000,
            });
            return parseOrThrow(ReminderSchema, data);
        },
    },
    email: {
        templates: {
            list: async (signal?: AbortSignal): Promise<ListResponse<EmailTemplate>> => {
                const data = await httpClient().request({ path: backendEndpoints.emailTemplatesList.path, timeoutMs: 12_000, signal });
                return parseOrThrow(ListResponseSchema(EmailTemplateResponseSchema), data);
            },
        },
        send: async (input: { to: string[]; templateId: string; subject?: string; html?: string }): Promise<EmailSendResponse> => {
            const data = await httpClient().request({
                path: backendEndpoints.emailSend.path,
                method: backendEndpoints.emailSend.method,
                body: {
                    to: input.to,
                    template_id: input.templateId,
                    subject: input.subject,
                    html: input.html,
                },
                timeoutMs: 30_000,
            });
            return parseOrThrow(EmailSendResponseSchema, data);
        },
    },
    voiceCalls: {
        guard: async (input: {
            tenantId?: string;
            partnerId?: string;
            requestedFeatures?: VoiceFeature[];
            callId?: string;
            providerCallId?: string;
            allowOverage?: boolean;
        }): Promise<VoiceCallGuardResponse> => {
            const data = await httpClient().request({
                path: backendEndpoints.voiceCallsGuard.path,
                method: backendEndpoints.voiceCallsGuard.method,
                body: {
                    tenant_id: input.tenantId,
                    partner_id: input.partnerId,
                    requested_features: input.requestedFeatures,
                    call_id: input.callId,
                    provider_call_id: input.providerCallId,
                    allow_overage: input.allowOverage,
                },
                timeoutMs: 12_000,
            });
            return parseOrThrow(VoiceCallGuardResponseSchema, data);
        },
        start: async (input: {
            tenantId?: string;
            partnerId?: string;
            requestedFeatures?: VoiceFeature[];
            callId?: string;
            providerCallId?: string;
            allowOverage?: boolean;
        }): Promise<VoiceCallStartResponse> => {
            const data = await httpClient().request({
                path: backendEndpoints.voiceCallsStart.path,
                method: backendEndpoints.voiceCallsStart.method,
                body: {
                    tenant_id: input.tenantId,
                    partner_id: input.partnerId,
                    requested_features: input.requestedFeatures,
                    call_id: input.callId,
                    provider_call_id: input.providerCallId,
                    allow_overage: input.allowOverage,
                },
                timeoutMs: 12_000,
            });
            return parseOrThrow(VoiceCallStartResponseSchema, data);
        },
    },
    assistantActions: {
        list: async (signal?: AbortSignal): Promise<ListResponse<AssistantAction>> => {
            const data = await httpClient().request({ path: backendEndpoints.assistantActionsList.path, timeoutMs: 12_000, signal });
            return parseOrThrow(ListResponseSchema(AssistantActionSchema), data);
        },
    },
    assistantRuns: {
        list: async (
            input: {
                page?: number;
                pageSize?: number;
                statuses?: Array<"pending" | "in_progress" | "completed" | "failed">;
                actionType?: string;
                leadId?: string;
                from?: string;
                to?: string;
                sortKey?: "createdAt" | "startedAt" | "completedAt" | "status" | "actionType" | "source" | "leadId";
                sortDir?: "asc" | "desc";
            },
            signal?: AbortSignal
        ): Promise<{ items: AssistantRun[]; total?: number; page?: number; page_size?: number }> => {
            const data = await httpClient().request({
                path: backendEndpoints.assistantRunsList.path,
                method: backendEndpoints.assistantRunsList.method,
                query: {
                    page: input.page,
                    page_size: input.pageSize,
                    status: input.statuses?.length ? input.statuses.join(",") : undefined,
                    action_type: input.actionType,
                    lead_id: input.leadId,
                    from: input.from,
                    to: input.to,
                    sort_key: input.sortKey,
                    sort_dir: input.sortDir,
                },
                timeoutMs: 12_000,
                signal,
            });
            return parseOrThrow(PaginatedResponseSchema(AssistantRunSchema), data);
        },
        retry: async (id: string): Promise<AssistantRun> => {
            const data = await httpClient().request({
                path: backendEndpoints.assistantRunsRetry.path.replace("{id}", encodeURIComponent(id)),
                method: backendEndpoints.assistantRunsRetry.method,
                timeoutMs: 12_000,
            });
            return parseOrThrow(AssistantRunSchema, data);
        },
    },
    assistant: {
        plan: async (input: { actionType: string; source?: string; leadId?: string; context?: Record<string, unknown> }): Promise<AssistantPlan> => {
            const data = await httpClient().request({
                path: backendEndpoints.assistantPlan.path,
                method: backendEndpoints.assistantPlan.method,
                body: {
                    action_type: input.actionType,
                    source: input.source ?? "dashboard",
                    lead_id: input.leadId,
                    context: input.context ?? {},
                },
                timeoutMs: 12_000,
            });
            return parseOrThrow(AssistantPlanSchema, data);
        },
        execute: async (input: { actionType: string; source?: string; leadId?: string; context?: Record<string, unknown> }): Promise<AssistantRun> => {
            const data = await httpClient().request({
                path: backendEndpoints.assistantExecute.path,
                method: backendEndpoints.assistantExecute.method,
                body: {
                    action_type: input.actionType,
                    source: input.source ?? "dashboard",
                    lead_id: input.leadId,
                    context: input.context ?? {},
                },
                timeoutMs: 12_000,
            });
            return parseOrThrow(AssistantRunSchema, data);
        },
    },
    admin: {
        auditLogs: {
            list: async (input: AuditLogsListInput, signal?: AbortSignal): Promise<{ items: AuditLogEvent[]; total?: number; page?: number; page_size?: number }> => {
                const data = await httpClient().request({
                    path: backendEndpoints.auditLogsList.path,
                    method: backendEndpoints.auditLogsList.method,
                    query: {
                        page: input.page,
                        page_size: input.pageSize,
                        event_type: input.eventType,
                        from: input.from,
                        to: input.to,
                        user: input.userQuery,
                        tenant_id: input.tenantId,
                        partner_id: input.partnerId,
                    },
                    timeoutMs: 12_000,
                    signal,
                });
                return parseOrThrow(PaginatedResponseSchema(AuditLogEventSchema), data);
            },
        },
        securityEvents: {
            list: async (input: SecurityEventsListInput, signal?: AbortSignal): Promise<{ items: SecurityEvent[]; total?: number; page?: number; page_size?: number }> => {
                const data = await httpClient().request({
                    path: backendEndpoints.securityEventsList.path,
                    method: backendEndpoints.securityEventsList.method,
                    query: {
                        page: input.page,
                        page_size: input.pageSize,
                        event_type: input.eventType,
                        severity: input.severity,
                        from: input.from,
                        to: input.to,
                        user: input.userQuery,
                        tenant_id: input.tenantId,
                        partner_id: input.partnerId,
                    },
                    timeoutMs: 12_000,
                    signal,
                });
                return parseOrThrow(PaginatedResponseSchema(SecurityEventSchema), data);
            },
        },
        partners: {
            list: async (input: AdminResourceListInput, signal?: AbortSignal): Promise<{ items: PartnerSummary[]; total?: number; page?: number; page_size?: number }> => {
                const data = await httpClient().request({
                    path: backendEndpoints.partnersList.path,
                    method: backendEndpoints.partnersList.method,
                    query: {
                        page: input.page,
                        page_size: input.pageSize,
                        q: input.query,
                        status: input.status,
                    },
                    timeoutMs: 12_000,
                    signal,
                });
                return parseOrThrow(PaginatedResponseSchema(PartnerSummarySchema), data);
            },
            suspend: async (input: { partnerId: string; reason?: string }): Promise<PartnerSummary> => {
                const data = await httpClient().request({
                    path: backendEndpoints.partnerSuspend.path.replace("{id}", encodeURIComponent(input.partnerId)),
                    method: backendEndpoints.partnerSuspend.method,
                    body: input.reason ? { reason: input.reason } : {},
                    timeoutMs: 12_000,
                });
                return parseOrThrow(PartnerSummarySchema, data);
            },
            reactivate: async (input: { partnerId: string; reason?: string }): Promise<PartnerSummary> => {
                const data = await httpClient().request({
                    path: backendEndpoints.partnerReactivate.path.replace("{id}", encodeURIComponent(input.partnerId)),
                    method: backendEndpoints.partnerReactivate.method,
                    body: input.reason ? { reason: input.reason } : {},
                    timeoutMs: 12_000,
                });
                return parseOrThrow(PartnerSummarySchema, data);
            },
        },
        tenants: {
            list: async (input: AdminResourceListInput, signal?: AbortSignal): Promise<{ items: TenantSummary[]; total?: number; page?: number; page_size?: number }> => {
                const data = await httpClient().request({
                    path: backendEndpoints.tenantsList.path,
                    method: backendEndpoints.tenantsList.method,
                    query: {
                        page: input.page,
                        page_size: input.pageSize,
                        q: input.query,
                        status: input.status,
                        partner_id: input.partnerId,
                    },
                    timeoutMs: 12_000,
                    signal,
                });
                return parseOrThrow(PaginatedResponseSchema(TenantSummarySchema), data);
            },
            suspend: async (input: { tenantId: string; reason?: string }): Promise<TenantSummary> => {
                const data = await httpClient().request({
                    path: backendEndpoints.tenantSuspend.path.replace("{id}", encodeURIComponent(input.tenantId)),
                    method: backendEndpoints.tenantSuspend.method,
                    body: input.reason ? { reason: input.reason } : {},
                    timeoutMs: 12_000,
                });
                return parseOrThrow(TenantSummarySchema, data);
            },
            reactivate: async (input: { tenantId: string; reason?: string }): Promise<TenantSummary> => {
                const data = await httpClient().request({
                    path: backendEndpoints.tenantReactivate.path.replace("{id}", encodeURIComponent(input.tenantId)),
                    method: backendEndpoints.tenantReactivate.method,
                    body: input.reason ? { reason: input.reason } : {},
                    timeoutMs: 12_000,
                });
                return parseOrThrow(TenantSummarySchema, data);
            },
        },
    },
};
