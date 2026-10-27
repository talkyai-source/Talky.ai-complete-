import { z } from "zod";

export interface ConnectorsTable {
    id: string;
    name: string;
    type: string;
    config: Record<string, unknown>;
    created_at: string;
}

export interface ConnectorAccountsTable {
    id: string;
    connector_id: string;
    credentials: Record<string, unknown>;
    metadata: Record<string, unknown>;
}

export interface MeetingsTable {
    id: string;
    title: string;
    participants: string[];
    start_time: string;
    end_time: string;
    status: string;
}

export interface RemindersTable {
    id: string;
    content: string;
    due_date?: string;
    is_completed?: boolean;
}

export interface AssistantActionsTable {
    id: string;
    name: string;
    description: string;
    parameters: Record<string, unknown>;
}

export const ConnectorSchema = z.object({
    id: z.string(),
    name: z.string(),
    type: z.string(),
    config: z.record(z.string(), z.unknown()),
    createdAt: z.string(),
});

export type Connector = z.infer<typeof ConnectorSchema>;

export const ConnectorResponseSchema = z.union([
    ConnectorSchema,
    z
        .object({
            id: z.string(),
            name: z.string(),
            type: z.string(),
            config: z.record(z.string(), z.unknown()),
            created_at: z.string(),
        })
        .transform((v) => ({ ...v, createdAt: v.created_at })),
]);

export const ConnectorAccountSchema = z.object({
    id: z.string(),
    connector_id: z.string(),
    credentials: z.record(z.string(), z.unknown()),
    metadata: z.record(z.string(), z.unknown()),
});

export type ConnectorAccount = z.infer<typeof ConnectorAccountSchema>;

export const ConnectorConnectionStatusSchema = z.enum(["connected", "disconnected", "expired", "error"] as const);

export type ConnectorConnectionStatus = z.infer<typeof ConnectorConnectionStatusSchema>;

export const ConnectorProviderStatusSchema = z.object({
    type: z.string(),
    status: ConnectorConnectionStatusSchema,
    last_sync: z.string().nullable().optional(),
    error_message: z.string().nullable().optional(),
    provider: z.string().nullable().optional(),
});

export type ConnectorProviderStatus = z.infer<typeof ConnectorProviderStatusSchema>;

export const SalesforceSettingsSchema = z.object({
    callback_campaign_id: z.string().nullable().optional(),
    log_calls: z.boolean(),
    create_leads: z.boolean(),
    sync_inbound: z.boolean(),
    callback_priority: z.number(),
});

export type SalesforceSettings = z.infer<typeof SalesforceSettingsSchema>;

export const SalesforceWebhookInfoSchema = z.object({
    callback_url: z.string().nullable().optional(),
    outbound_message_url: z.string().nullable().optional(),
    token_set: z.boolean(),
    token_masked: z.string().nullable().optional(),
    token: z.string().nullable().optional(),
});

export const SalesforceSettingsResponseSchema = z.object({
    server_configured: z.boolean(),
    connected: z.boolean(),
    status: z.string(),
    connector_id: z.string().nullable().optional(),
    org_id: z.string().nullable().optional(),
    username: z.string().nullable().optional(),
    instance_url: z.string().nullable().optional(),
    api_version: z.string(),
    login_url: z.string(),
    settings: SalesforceSettingsSchema,
    webhook: SalesforceWebhookInfoSchema,
    last_synced_call_at: z.string().nullable().optional(),
});

export type SalesforceSettingsResponse = z.infer<typeof SalesforceSettingsResponseSchema>;

export const SalesforceProbeSchema = z.object({
    ok: z.boolean(),
    org_id: z.string().nullable().optional(),
    username: z.string().nullable().optional(),
    instance_url: z.string().nullable().optional(),
    api_version: z.string().nullable().optional(),
    error: z.string().nullable().optional(),
});

export type SalesforceProbe = z.infer<typeof SalesforceProbeSchema>;

export const SalesforceImportResultSchema = z.object({
    campaign_id: z.string(),
    list_id: z.string().nullable().optional(),
    fetched: z.number(),
    imported: z.number(),
    revived: z.number(),
    duplicates_skipped: z.number(),
    invalid: z.number(),
    errors: z.array(z.record(z.string(), z.unknown())).optional(),
});

export type SalesforceImportResult = z.infer<typeof SalesforceImportResultSchema>;

export const EmailTemplateSchema = z.object({
    id: z.string(),
    name: z.string(),
    html: z.string(),
    thumbnailUrl: z.string().url().optional(),
    locked: z.boolean().optional(),
    updatedAt: z.string().optional(),
});

export type EmailTemplate = z.infer<typeof EmailTemplateSchema>;

export const EmailTemplateResponseSchema = z.union([
    EmailTemplateSchema,
    z
        .object({
            id: z.string(),
            name: z.string(),
            html_content: z.string(),
            thumbnail_url: z.string().url().nullable().optional(),
            locked: z.boolean().optional(),
            is_locked: z.boolean().optional(),
            updated_at: z.string().nullable().optional(),
            updatedAt: z.string().nullable().optional(),
        })
        .transform((v) => ({
            id: v.id,
            name: v.name,
            html: v.html_content,
            thumbnailUrl: v.thumbnail_url ?? undefined,
            locked: v.locked ?? v.is_locked ?? undefined,
            updatedAt: v.updatedAt ?? v.updated_at ?? undefined,
        })),
    z
        .object({
            id: z.string(),
            name: z.string(),
            html: z.string(),
            thumbnailUrl: z.string().url().nullable().optional(),
            locked: z.boolean().optional(),
            updatedAt: z.string().nullable().optional(),
        })
        .transform((v) => ({
            ...v,
            thumbnailUrl: v.thumbnailUrl ?? undefined,
            updatedAt: v.updatedAt ?? undefined,
        })),
]);

export const EmailSendResponseSchema = z
    .object({
        messageId: z.string().optional(),
        message_id: z.string().optional(),
        status: z.string().optional(),
    })
    .passthrough()
    .transform((v) => ({
        messageId: v.messageId ?? v.message_id,
        status: v.status,
    }));

export type EmailSendResponse = z.infer<typeof EmailSendResponseSchema>;

export const MeetingSchema = z.object({
    id: z.string(),
    title: z.string(),
    participants: z.array(z.string()),
    startTime: z.string(),
    endTime: z.string(),
    status: z.string(),
});

export type Meeting = z.infer<typeof MeetingSchema>;

export const MeetingResponseSchema = z.union([
    MeetingSchema,
    z
        .object({
            id: z.string(),
            title: z.string(),
            participants: z.array(z.string()),
            start_time: z.string(),
            end_time: z.string(),
            status: z.string(),
        })
        .transform((v) => ({ ...v, startTime: v.start_time, endTime: v.end_time })),
]);

export const CalendarEventParticipantSchema = z
    .union([
        z.string().transform((name) => ({ name })),
        z
            .object({
                id: z.string().optional(),
                name: z.string().optional(),
                email: z.string().optional(),
                role: z.string().optional(),
            })
            .transform((p) => ({
                id: p.id,
                name: p.name ?? undefined,
                email: p.email ?? undefined,
                role: p.role ?? undefined,
            })),
    ])
    .transform((p) => ({
        id: "id" in p ? p.id : undefined,
        name: "name" in p ? p.name : undefined,
        email: "email" in p ? p.email : undefined,
        role: "role" in p ? p.role : undefined,
    }));

export const CalendarEventSchema = z.object({
    id: z.string(),
    title: z.string(),
    startTime: z.string(),
    endTime: z.string().optional(),
    status: z.string().optional(),
    leadId: z.string().optional(),
    leadName: z.string().optional(),
    notes: z.string().nullable().optional(),
    participants: z.array(CalendarEventParticipantSchema).optional(),
    joinLink: z.string().url().optional(),
    calendarLink: z.string().url().optional(),
});

export type CalendarEvent = z.infer<typeof CalendarEventSchema>;

export const CalendarEventResponseSchema = z.union([
    CalendarEventSchema,
    z
        .object({
            id: z.string(),
            title: z.string(),
            start_time: z.string(),
            end_time: z.string().nullable().optional(),
            status: z.string().nullable().optional(),
            lead_id: z.string().nullable().optional(),
            lead_name: z.string().nullable().optional(),
            notes: z.string().nullable().optional(),
            participants: z.array(CalendarEventParticipantSchema).optional(),
            join_link: z.string().url().nullable().optional(),
            calendar_link: z.string().url().nullable().optional(),
        })
        .transform((v) => ({
            id: v.id,
            title: v.title,
            startTime: v.start_time,
            endTime: v.end_time ?? undefined,
            status: v.status ?? undefined,
            leadId: v.lead_id ?? undefined,
            leadName: v.lead_name ?? undefined,
            notes: v.notes ?? undefined,
            participants: v.participants,
            joinLink: v.join_link ?? undefined,
            calendarLink: v.calendar_link ?? undefined,
        })),
    z
        .object({
            id: z.string(),
            title: z.string(),
            startTime: z.string(),
            endTime: z.string().nullable().optional(),
            status: z.string().nullable().optional(),
            leadId: z.string().nullable().optional(),
            leadName: z.string().nullable().optional(),
            notes: z.string().nullable().optional(),
            participants: z.array(CalendarEventParticipantSchema).optional(),
            joinLink: z.string().url().nullable().optional(),
            calendarLink: z.string().url().nullable().optional(),
        })
        .transform((v) => ({
            id: v.id,
            title: v.title,
            startTime: v.startTime,
            endTime: v.endTime ?? undefined,
            status: v.status ?? undefined,
            leadId: v.leadId ?? undefined,
            leadName: v.leadName ?? undefined,
            notes: v.notes ?? undefined,
            participants: v.participants,
            joinLink: v.joinLink ?? undefined,
            calendarLink: v.calendarLink ?? undefined,
        })),
]);

export const ReminderStatusSchema = z.enum(["scheduled", "sent", "failed", "canceled"]);

export type ReminderStatus = z.infer<typeof ReminderStatusSchema>;

export const ReminderChannelSchema = z.enum(["email", "sms"]);

export type ReminderChannel = z.infer<typeof ReminderChannelSchema>;

const ReminderNormalizedSchema = z.object({
    id: z.string(),
    content: z.string(),
    status: ReminderStatusSchema,
    channel: ReminderChannelSchema,
    scheduledAt: z.string(),
    meetingId: z.string().optional(),
    meetingTitle: z.string().optional(),
    contactId: z.string().optional(),
    contactName: z.string().optional(),
    toEmail: z.string().optional(),
    toPhone: z.string().optional(),
    sentAt: z.string().optional(),
    failedAt: z.string().optional(),
    canceledAt: z.string().optional(),
    retryCount: z.number().int().nonnegative().optional(),
    maxRetries: z.number().int().positive().optional(),
    nextRetryAt: z.string().optional(),
    failureReason: z.string().optional(),
    createdAt: z.string().optional(),
    updatedAt: z.string().optional(),
});

const ReminderSnakeSchema = z
    .object({
        id: z.string(),
        content: z.string(),
        status: ReminderStatusSchema.optional(),
        channel: ReminderChannelSchema.optional(),
        scheduled_at: z.string().optional(),
        meeting_id: z.string().nullable().optional(),
        meeting_title: z.string().nullable().optional(),
        contact_id: z.string().nullable().optional(),
        contact_name: z.string().nullable().optional(),
        to_email: z.string().nullable().optional(),
        to_phone: z.string().nullable().optional(),
        sent_at: z.string().nullable().optional(),
        failed_at: z.string().nullable().optional(),
        canceled_at: z.string().nullable().optional(),
        retry_count: z.number().int().nonnegative().nullable().optional(),
        max_retries: z.number().int().positive().nullable().optional(),
        next_retry_at: z.string().nullable().optional(),
        failure_reason: z.string().nullable().optional(),
        created_at: z.string().nullable().optional(),
        updated_at: z.string().nullable().optional(),
    })
    .transform((v) => ({
        id: v.id,
        content: v.content,
        status: v.status ?? "scheduled",
        channel: v.channel ?? "email",
        scheduledAt: v.scheduled_at ?? v.created_at ?? new Date().toISOString(),
        meetingId: v.meeting_id ?? undefined,
        meetingTitle: v.meeting_title ?? undefined,
        contactId: v.contact_id ?? undefined,
        contactName: v.contact_name ?? undefined,
        toEmail: v.to_email ?? undefined,
        toPhone: v.to_phone ?? undefined,
        sentAt: v.sent_at ?? undefined,
        failedAt: v.failed_at ?? undefined,
        canceledAt: v.canceled_at ?? undefined,
        retryCount: v.retry_count ?? undefined,
        maxRetries: v.max_retries ?? undefined,
        nextRetryAt: v.next_retry_at ?? undefined,
        failureReason: v.failure_reason ?? undefined,
        createdAt: v.created_at ?? undefined,
        updatedAt: v.updated_at ?? undefined,
    }));

const ReminderLegacySchema = z
    .object({
        id: z.string(),
        content: z.string(),
        due_date: z.string(),
        is_completed: z.boolean(),
    })
    .transform((v) => ({
        id: v.id,
        content: v.content,
        status: v.is_completed ? ("sent" as const) : ("scheduled" as const),
        channel: "email" as const,
        scheduledAt: v.due_date,
        meetingId: undefined,
        meetingTitle: undefined,
        contactId: undefined,
        contactName: undefined,
        toEmail: undefined,
        toPhone: undefined,
        sentAt: v.is_completed ? v.due_date : undefined,
        failedAt: undefined,
        canceledAt: undefined,
        retryCount: undefined,
        maxRetries: undefined,
        nextRetryAt: undefined,
        failureReason: undefined,
        createdAt: undefined,
        updatedAt: undefined,
    }));

export const ReminderSchema = z.union([ReminderNormalizedSchema, ReminderSnakeSchema, ReminderLegacySchema]);

export type Reminder = z.infer<typeof ReminderSchema>;

export const AssistantRunStatusSchema = z.enum(["pending", "running", "in_progress", "scheduled", "completed", "failed", "unknown", "cancelled"]);

export type AssistantRunStatus = z.infer<typeof AssistantRunStatusSchema>;

const AssistantRunStatusInputSchema = z
    .enum(["pending", "running", "in_progress", "scheduled", "completed", "failed", "unknown", "cancelled", "success"])
    .transform((v) => (v === "success" ? "completed" : v));

export const ActionReceiptSchema = z.object({
    provider: z.string().max(256).optional(),
    connector_id: z.string().max(256).optional(),
    external_account_id: z.string().max(512).optional(),
    message_id: z.string().max(512).optional(),
    external_event_id: z.string().max(512).optional(),
    meeting_id: z.string().max(256).optional(),
    reminder_id: z.string().max(256).optional(),
    plan_id: z.string().max(256).optional(),
    job_id: z.string().max(256).optional(),
    child_action_id: z.string().max(512).optional(),
    provider_status: z.string().max(64).optional(),
});

export type ActionReceipt = z.infer<typeof ActionReceiptSchema>;

export const AssistantRunSchema = z.object({
        id: z.string(),
        type: z.string(),
        status: AssistantRunStatusInputSchema,
        triggered_by: z.string().nullable().optional(),
        lead_id: z.string().nullable().optional(),
        created_at: z.string(),
        started_at: z.string().nullable().optional(),
        completed_at: z.string().nullable().optional(),
        confirmation_allowed: z.boolean().optional(),
        receipt: ActionReceiptSchema.nullable().optional(),
        error: z.string().nullable().optional(),
    }).transform((value) => ({
        id: value.id, actionType: value.type, status: value.status,
        source: value.triggered_by ?? "unknown", leadId: value.lead_id,
        createdAt: value.created_at, startedAt: value.started_at, completedAt: value.completed_at,
        confirmationAllowed: value.confirmation_allowed, receipt: value.receipt ?? undefined,
        error: value.error,
    }));

export type AssistantRun = z.infer<typeof AssistantRunSchema>;

export const AssistantActionsResponseSchema = z.object({
    actions: z.array(AssistantRunSchema), total: z.number().int().nonnegative(),
    page: z.number().int().positive(), page_size: z.number().int().positive(),
});

export const PaginatedResponseSchema = <T extends z.ZodTypeAny>(item: T) =>
    z
        .object({
            items: z.array(item),
            total: z.number().optional(),
            page: z.number().optional(),
            page_size: z.number().optional(),
            pageSize: z.number().optional(),
        })
        .passthrough()
        .transform((v) => {
            const out: { items: z.infer<ReturnType<typeof z.array<T>>>; total?: number; page?: number; page_size?: number } = {
                items: v.items,
            };
            if (typeof v.total === "number") out.total = v.total;
            if (typeof v.page === "number") out.page = v.page;
            const ps = v.page_size ?? v.pageSize;
            if (typeof ps === "number") out.page_size = ps;
            return out;
        });

export const ListResponseSchema = <T extends z.ZodTypeAny>(item: T) =>
    z.object({
        items: z.array(item),
    });

export type ListResponse<T> = { items: T[] };

export const AuditSeveritySchema = z.enum(["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]);
export type AuditSeverity = z.infer<typeof AuditSeveritySchema>;
export const SecurityEventStatusSchema = z.enum(["open", "investigating", "resolved", "false_positive", "escalated"]);
export type SecurityEventStatus = z.infer<typeof SecurityEventStatusSchema>;

export const SuspensionStatusSchema = z.enum(["active", "suspended"]);
export type SuspensionStatus = z.infer<typeof SuspensionStatusSchema>;

// Keep the original microseconds for tuple pagination; never replace this with
// a Date/toISOString conversion or a fabricated epoch fallback.
const EventTimeSchema = z.string().datetime({ offset: true });
export const EventBoundarySchema = z.object({ time: EventTimeSchema, id: z.guid() });
export type EventBoundary = z.infer<typeof EventBoundarySchema>;

export const CanonicalAuditLogSchema = z.object({
    event_id: z.guid(), event_time: EventTimeSchema,
    event_type: z.string().min(1), event_category: z.string().min(1), severity: AuditSeveritySchema,
    actor_id: z.guid().nullable(), actor_type: z.string().min(1), tenant_id: z.guid().nullable(),
    resource_type: z.string().nullable(), resource_id: z.guid().nullable(),
    action: z.string(), description: z.string().nullable(), ip_address: z.string().nullable(),
    metadata: z.record(z.string(), z.unknown()).nullable(),
});
export const AuditLogEventSchema = CanonicalAuditLogSchema.transform(value => ({
    id: value.event_id, timestamp: value.event_time, eventType: value.event_type,
    eventCategory: value.event_category, severity: value.severity, actionType: value.action,
    actor: { id: value.actor_id ?? undefined, type: value.actor_type },
    target: value.resource_type ? { type: value.resource_type, id: value.resource_id ?? undefined } : undefined,
    tenantId: value.tenant_id ?? undefined, description: value.description, ipAddress: value.ip_address,
    metadata: value.metadata ?? undefined,
}));
export type AuditLogEvent = z.infer<typeof AuditLogEventSchema>;

export const SecurityEventSchema = z.object({
    event_id: z.guid(), created_at: EventTimeSchema, event_type: z.string().min(1),
    severity: AuditSeveritySchema, status: SecurityEventStatusSchema,
    tenant_id: z.guid().nullable(), user_id: z.guid().nullable(),
    title: z.string(), description: z.string().nullable(), detection_source: z.string(),
    evidence: z.record(z.string(), z.unknown()).nullable(),
}).transform(value => ({
    id: value.event_id, timestamp: value.created_at, eventType: value.event_type,
    severity: value.severity, status: value.status, tenantId: value.tenant_id ?? undefined,
    subjectId: value.user_id ?? undefined, title: value.title, description: value.description,
    detectionSource: value.detection_source, metadata: value.evidence ?? undefined,
}));
export type SecurityEvent = z.infer<typeof SecurityEventSchema>;

const PartnerDirectoryStatusSchema = z.enum(["active", "suspended", "pending_deletion", "unknown"]);

export const PartnerSummarySchema = z.union([
    z
        .object({
            id: z.string(),
            name: z.string(),
            status: PartnerDirectoryStatusSchema,
            suspendedAt: z.string().optional().nullable(),
            tenantCount: z.number().optional(),
            updatedAt: z.string().optional().nullable(),
            suspended_at: z.string().optional().nullable(),
            tenant_count: z.number().optional(),
            updated_at: z.string().optional().nullable(),
        })
        .passthrough()
        .transform((v) => ({
            id: v.id,
            name: v.name,
            status: v.status,
            suspendedAt: (v.suspendedAt !== undefined ? v.suspendedAt : v.suspended_at) ?? undefined,
            tenantCount: v.tenantCount ?? v.tenant_count,
            updatedAt: (v.updatedAt !== undefined ? v.updatedAt : v.updated_at) ?? undefined,
        })),
    z
        .object({
            id: z.string().optional(),
            partner_id: z.string().optional(),
            name: z.string().optional(),
            display_name: z.string().optional(),
            status: PartnerDirectoryStatusSchema,
            suspended_at: z.string().optional().nullable(),
            tenant_count: z.number().optional(),
            updated_at: z.string().optional().nullable(),
        })
        .passthrough()
        .transform((v) => ({
            id: v.id ?? v.partner_id ?? "",
            name: v.name ?? v.display_name ?? v.partner_id ?? "",
            status: v.status,
            suspendedAt: v.suspended_at ?? undefined,
            tenantCount: v.tenant_count,
            updatedAt: v.updated_at ?? undefined,
        })),
]);

export type PartnerSummary = z.infer<typeof PartnerSummarySchema>;

const TenantSubscriptionStatusSchema = z.enum(["active", "trialing", "inactive", "past_due", "suspended", "cancelled"]);

export const TenantMutationReceiptSchema = z.object({
    id: z.string(),
    status: z.enum(["active", "suspended"]),
    updated_at: z.string().nullable(),
    changed: z.boolean(),
    audit_event_id: z.guid().nullable(),
}).refine((value) => !value.changed || value.audit_event_id !== null, "Changed tenant has no durable audit receipt")
    .transform((value) => ({ id: value.id, status: value.status, updatedAt: value.updated_at,
        changed: value.changed, auditEventId: value.audit_event_id }));
export type TenantMutationReceipt = z.infer<typeof TenantMutationReceiptSchema>;

export const TenantSummarySchema = z.union([
    z
        .object({
            id: z.string(),
            name: z.string(),
            partnerId: z.string().optional().nullable(),
            status: TenantSubscriptionStatusSchema,
            suspendedAt: z.string().optional().nullable(),
            updatedAt: z.string().optional().nullable(),
        })
        .passthrough()
        .transform((v) => ({
            id: v.id,
            name: v.name,
            partnerId: v.partnerId ?? undefined,
            status: v.status,
            suspendedAt: v.suspendedAt ?? undefined,
            updatedAt: v.updatedAt,
        })),
    z
        .object({
            id: z.string(),
            name: z.string().optional(),
            tenant_name: z.string().optional(),
            business_name: z.string().optional(),
            partner_id: z.string().optional().nullable(),
            status: TenantSubscriptionStatusSchema,
            suspended_at: z.string().optional().nullable(),
            updated_at: z.string().optional().nullable(),
        })
        .passthrough()
        .transform((v) => ({
            id: v.id,
            name: v.name ?? v.business_name ?? v.tenant_name ?? v.id,
            partnerId: v.partner_id ?? undefined,
            status: v.status,
            suspendedAt: v.suspended_at ?? undefined,
            updatedAt: v.updated_at,
        })),
]);

export type TenantSummary = z.infer<typeof TenantSummarySchema>;

export const VoiceFeatureSchema = z.enum(["voice", "premium", "transfer"]);

export type VoiceFeature = z.infer<typeof VoiceFeatureSchema>;

const VoiceCallActiveCallsSchema = z.object({
    tenant: z.number(),
    partner: z.number(),
});

const VoiceCallOverageSchema = z.object({
    tenant: z.boolean(),
    partner: z.boolean(),
});

const VoiceCallGuardRejectedCamelSchema = z.object({
    outcome: z.literal("REJECT"),
    tenantId: z.string().nullable().optional(),
    partnerId: z.string().nullable().optional(),
    code: z.string(),
    reason: z.string(),
    retryAfterSeconds: z.number().nullable().optional(),
    blockExpiresAt: z.string().nullable().optional(),
});

const VoiceCallGuardRejectedSnakeSchema = z
    .object({
        outcome: z.literal("REJECT"),
        tenant_id: z.string().nullable().optional(),
        partner_id: z.string().nullable().optional(),
        code: z.string(),
        reason: z.string(),
        retry_after_seconds: z.number().nullable().optional(),
        block_expires_at: z.string().nullable().optional(),
    })
    .transform((v) => ({
        outcome: v.outcome,
        tenantId: v.tenant_id ?? null,
        partnerId: v.partner_id ?? null,
        code: v.code,
        reason: v.reason,
        retryAfterSeconds: v.retry_after_seconds ?? null,
        blockExpiresAt: v.block_expires_at ?? null,
    }));

const VoiceCallGuardAllowedCamelSchema = z.object({
    outcome: z.literal("ALLOW"),
    tenantId: z.string(),
    partnerId: z.string(),
    reservationId: z.string().nullable(),
    activeCalls: VoiceCallActiveCallsSchema,
    overage: VoiceCallOverageSchema,
    allowedFeatures: z.array(VoiceFeatureSchema),
    requestedFeatures: z.array(VoiceFeatureSchema),
    usageAccountId: z.string().nullable(),
    billingAccountId: z.string().nullable(),
});

const VoiceCallGuardAllowedSnakeSchema = z
    .object({
        outcome: z.literal("ALLOW"),
        tenant_id: z.string(),
        partner_id: z.string(),
        reservation_id: z.string().nullable(),
        active_calls: VoiceCallActiveCallsSchema,
        overage: VoiceCallOverageSchema,
        allowed_features: z.array(VoiceFeatureSchema),
        requested_features: z.array(VoiceFeatureSchema),
        usage_account_id: z.string().nullable(),
        billing_account_id: z.string().nullable(),
    })
    .transform((v) => ({
        outcome: v.outcome,
        tenantId: v.tenant_id,
        partnerId: v.partner_id,
        reservationId: v.reservation_id,
        activeCalls: v.active_calls,
        overage: v.overage,
        allowedFeatures: v.allowed_features,
        requestedFeatures: v.requested_features,
        usageAccountId: v.usage_account_id,
        billingAccountId: v.billing_account_id,
    }));

export const VoiceCallGuardResponseSchema = z.union([
    VoiceCallGuardRejectedCamelSchema,
    VoiceCallGuardRejectedSnakeSchema,
    VoiceCallGuardAllowedCamelSchema,
    VoiceCallGuardAllowedSnakeSchema,
]);

export type VoiceCallGuardResponse = z.infer<typeof VoiceCallGuardResponseSchema>;

const VoiceCallStartAllowedCamelSchema = VoiceCallGuardAllowedCamelSchema.extend({
    callId: z.string(),
    providerCallId: z.string().nullable(),
    status: z.literal("active"),
    startedAt: z.string(),
});

const VoiceCallStartAllowedSnakeSchema = z
    .object({
        outcome: z.literal("ALLOW"),
        tenant_id: z.string(),
        partner_id: z.string(),
        reservation_id: z.string().nullable(),
        call_id: z.string(),
        provider_call_id: z.string().nullable(),
        status: z.literal("active"),
        started_at: z.string(),
        active_calls: VoiceCallActiveCallsSchema,
        overage: VoiceCallOverageSchema,
        allowed_features: z.array(VoiceFeatureSchema),
        requested_features: z.array(VoiceFeatureSchema),
        usage_account_id: z.string().nullable(),
        billing_account_id: z.string().nullable(),
    })
    .transform((v) => ({
        outcome: v.outcome,
        tenantId: v.tenant_id,
        partnerId: v.partner_id,
        reservationId: v.reservation_id,
        callId: v.call_id,
        providerCallId: v.provider_call_id,
        status: v.status,
        startedAt: v.started_at,
        activeCalls: v.active_calls,
        overage: v.overage,
        allowedFeatures: v.allowed_features,
        requestedFeatures: v.requested_features,
        usageAccountId: v.usage_account_id,
        billingAccountId: v.billing_account_id,
    }));

export const VoiceCallStartResponseSchema = z.union([
    VoiceCallGuardRejectedCamelSchema,
    VoiceCallGuardRejectedSnakeSchema,
    VoiceCallStartAllowedCamelSchema,
    VoiceCallStartAllowedSnakeSchema,
]);

export type VoiceCallStartResponse = z.infer<typeof VoiceCallStartResponseSchema>;
