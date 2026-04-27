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
    config: z.record(z.unknown()),
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
            config: z.record(z.unknown()),
            created_at: z.string(),
        })
        .transform((v) => ({ ...v, createdAt: v.created_at })),
]);

export const ConnectorAccountSchema = z.object({
    id: z.string(),
    connector_id: z.string(),
    credentials: z.record(z.unknown()),
    metadata: z.record(z.unknown()),
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

export const AssistantActionSchema = z.object({
    id: z.string(),
    name: z.string(),
    description: z.string(),
    parameters: z.record(z.unknown()),
});

export type AssistantAction = z.infer<typeof AssistantActionSchema>;

export const AssistantRunStatusSchema = z.enum(["pending", "in_progress", "completed", "failed"]);

export type AssistantRunStatus = z.infer<typeof AssistantRunStatusSchema>;

const AssistantRunStatusInputSchema = z
    .enum(["pending", "in_progress", "completed", "failed", "success"])
    .transform((v) => (v === "success" ? "completed" : v));

const AssistantRunBaseSchema = z.object({
    id: z.string(),
    actionType: z.string(),
    source: z.string(),
    leadId: z.string().nullable().optional(),
    status: AssistantRunStatusInputSchema,
    createdAt: z.string(),
    startedAt: z.string().nullable().optional(),
    completedAt: z.string().nullable().optional(),
    result: z.string().nullable().optional(),
    requestPayload: z.unknown().optional(),
    responsePayload: z.unknown().optional(),
    error: z.unknown().optional(),
});

export const AssistantRunSchema = z.union([
    AssistantRunBaseSchema,
    z
        .object({
            id: z.string(),
            action_type: z.string(),
            source: z.string().optional().default("unknown"),
            lead_id: z.string().nullable().optional(),
            status: AssistantRunStatusInputSchema,
            created_at: z.string(),
            started_at: z.string().nullable().optional(),
            completed_at: z.string().nullable().optional(),
            result: z.string().nullable().optional(),
            request_payload: z.unknown().optional(),
            response_payload: z.unknown().optional(),
            error: z.unknown().optional(),
        })
        .transform((v) => ({
            id: v.id,
            actionType: v.action_type,
            source: v.source,
            leadId: v.lead_id ?? undefined,
            status: v.status,
            createdAt: v.created_at,
            startedAt: v.started_at ?? undefined,
            completedAt: v.completed_at ?? undefined,
            result: v.result ?? undefined,
            requestPayload: v.request_payload,
            responsePayload: v.response_payload,
            error: v.error,
        })),
    z
        .object({
            id: z.string(),
            actionType: z.string(),
            source: z.string().optional().default("unknown"),
            leadId: z.string().nullable().optional(),
            status: AssistantRunStatusInputSchema,
            createdAt: z.string(),
            startedAt: z.string().nullable().optional(),
            completedAt: z.string().nullable().optional(),
            result: z.string().nullable().optional(),
            requestPayload: z.unknown().optional(),
            responsePayload: z.unknown().optional(),
            error: z.unknown().optional(),
        })
        .transform((v) => ({
            id: v.id,
            actionType: v.actionType,
            source: v.source ?? "unknown",
            leadId: v.leadId ?? undefined,
            status: v.status,
            createdAt: v.createdAt,
            startedAt: v.startedAt ?? undefined,
            completedAt: v.completedAt ?? undefined,
            result: v.result ?? undefined,
            requestPayload: v.requestPayload,
            responsePayload: v.responsePayload,
            error: v.error,
        })),
]);

export type AssistantRun = z.infer<typeof AssistantRunSchema>;

export const AssistantPlanSchema = z
    .object({
        planId: z.string().optional(),
        steps: z.array(z.unknown()).optional(),
        estimatedImpact: z.unknown().optional(),
        summary: z.string().optional(),
    })
    .passthrough();

export type AssistantPlan = z.infer<typeof AssistantPlanSchema>;

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

export const AuditSeveritySchema = z.enum(["low", "medium", "high"]);

export type AuditSeverity = z.infer<typeof AuditSeveritySchema>;

export const SuspensionStatusSchema = z.enum(["active", "suspended"]);

export type SuspensionStatus = z.infer<typeof SuspensionStatusSchema>;

const AuditActorSchema = z
    .object({
        id: z.string().optional().nullable(),
        name: z.string().optional().nullable(),
        email: z.string().optional().nullable(),
    })
    .passthrough()
    .transform((v) => ({
        id: v.id ?? undefined,
        name: v.name ?? undefined,
        email: v.email ?? undefined,
    }));

const AuditTargetSchema = z
    .object({
        type: z.string(),
        id: z.string().optional().nullable(),
        name: z.string().optional().nullable(),
    })
    .passthrough()
    .transform((v) => ({
        type: v.type,
        id: v.id ?? undefined,
        name: v.name ?? undefined,
    }));

const AuditMetadataSchema = z.record(z.unknown());

export const AuditLogEventSchema = z.union([
    z
        .object({
            id: z.string(),
            timestamp: z.string(),
            actionType: z.string(),
            actor: AuditActorSchema.optional(),
            target: AuditTargetSchema.optional(),
            eventType: z.string().optional(),
            tenantId: z.string().optional().nullable(),
            partnerId: z.string().optional().nullable(),
            metadata: AuditMetadataSchema.optional().nullable(),
        })
        .passthrough()
        .transform((v) => ({
            id: v.id,
            timestamp: v.timestamp,
            actionType: v.actionType,
            actor: v.actor,
            target: v.target,
            eventType: v.eventType ?? v.actionType,
            tenantId: v.tenantId ?? undefined,
            partnerId: v.partnerId ?? undefined,
            metadata: v.metadata ?? undefined,
        })),
    z
        .object({
            id: z.string(),
            created_at: z.string().optional(),
            timestamp: z.string().optional(),
            action_type: z.string(),
            actor_id: z.string().optional().nullable(),
            actor_name: z.string().optional().nullable(),
            actor_email: z.string().optional().nullable(),
            actor: AuditActorSchema.optional(),
            target_type: z.string().optional(),
            target_id: z.string().optional().nullable(),
            target_name: z.string().optional().nullable(),
            target: AuditTargetSchema.optional(),
            event_type: z.string().optional(),
            tenant_id: z.string().optional().nullable(),
            partner_id: z.string().optional().nullable(),
            metadata: AuditMetadataSchema.optional().nullable(),
        })
        .passthrough()
        .transform((v) => ({
            id: v.id,
            timestamp: v.timestamp ?? v.created_at ?? new Date(0).toISOString(),
            actionType: v.action_type,
            actor: v.actor ?? (v.actor_id || v.actor_name || v.actor_email ? { id: v.actor_id ?? undefined, name: v.actor_name ?? undefined, email: v.actor_email ?? undefined } : undefined),
            target: v.target ?? (v.target_type ? { type: v.target_type, id: v.target_id ?? undefined, name: v.target_name ?? undefined } : undefined),
            eventType: v.event_type ?? v.action_type,
            tenantId: v.tenant_id ?? undefined,
            partnerId: v.partner_id ?? undefined,
            metadata: v.metadata ?? undefined,
        })),
]);

export type AuditLogEvent = z.infer<typeof AuditLogEventSchema>;

export const SecurityEventSchema = z.union([
    z
        .object({
            id: z.string(),
            timestamp: z.string(),
            eventType: z.string(),
            severity: AuditSeveritySchema,
            actor: AuditActorSchema.optional(),
            target: AuditTargetSchema.optional(),
            tenantId: z.string().optional().nullable(),
            partnerId: z.string().optional().nullable(),
            metadata: AuditMetadataSchema.optional().nullable(),
        })
        .passthrough()
        .transform((v) => ({
            id: v.id,
            timestamp: v.timestamp,
            eventType: v.eventType,
            severity: v.severity,
            actor: v.actor,
            target: v.target,
            tenantId: v.tenantId ?? undefined,
            partnerId: v.partnerId ?? undefined,
            metadata: v.metadata ?? undefined,
        })),
    z
        .object({
            id: z.string(),
            created_at: z.string().optional(),
            timestamp: z.string().optional(),
            event_type: z.string(),
            severity: AuditSeveritySchema,
            actor_id: z.string().optional().nullable(),
            actor_name: z.string().optional().nullable(),
            actor_email: z.string().optional().nullable(),
            actor: AuditActorSchema.optional(),
            target_type: z.string().optional(),
            target_id: z.string().optional().nullable(),
            target_name: z.string().optional().nullable(),
            target: AuditTargetSchema.optional(),
            tenant_id: z.string().optional().nullable(),
            partner_id: z.string().optional().nullable(),
            metadata: AuditMetadataSchema.optional().nullable(),
        })
        .passthrough()
        .transform((v) => ({
            id: v.id,
            timestamp: v.timestamp ?? v.created_at ?? new Date(0).toISOString(),
            eventType: v.event_type,
            severity: v.severity,
            actor: v.actor ?? (v.actor_id || v.actor_name || v.actor_email ? { id: v.actor_id ?? undefined, name: v.actor_name ?? undefined, email: v.actor_email ?? undefined } : undefined),
            target: v.target ?? (v.target_type ? { type: v.target_type, id: v.target_id ?? undefined, name: v.target_name ?? undefined } : undefined),
            tenantId: v.tenant_id ?? undefined,
            partnerId: v.partner_id ?? undefined,
            metadata: v.metadata ?? undefined,
        })),
]);

export type SecurityEvent = z.infer<typeof SecurityEventSchema>;

export const PartnerSummarySchema = z.union([
    z
        .object({
            id: z.string(),
            name: z.string(),
            status: SuspensionStatusSchema,
            suspendedAt: z.string().optional().nullable(),
            tenantCount: z.number().optional(),
            updatedAt: z.string().optional().nullable(),
        })
        .passthrough()
        .transform((v) => ({
            id: v.id,
            name: v.name,
            status: v.status,
            suspendedAt: v.suspendedAt ?? undefined,
            tenantCount: v.tenantCount,
            updatedAt: v.updatedAt ?? undefined,
        })),
    z
        .object({
            id: z.string().optional(),
            partner_id: z.string().optional(),
            name: z.string().optional(),
            display_name: z.string().optional(),
            status: SuspensionStatusSchema,
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

export const TenantSummarySchema = z.union([
    z
        .object({
            id: z.string(),
            name: z.string(),
            partnerId: z.string().optional().nullable(),
            status: SuspensionStatusSchema,
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
            updatedAt: v.updatedAt ?? undefined,
        })),
    z
        .object({
            id: z.string(),
            name: z.string().optional(),
            tenant_name: z.string().optional(),
            partner_id: z.string().optional().nullable(),
            status: SuspensionStatusSchema,
            suspended_at: z.string().optional().nullable(),
            updated_at: z.string().optional().nullable(),
        })
        .passthrough()
        .transform((v) => ({
            id: v.id,
            name: v.name ?? v.tenant_name ?? v.id,
            partnerId: v.partner_id ?? undefined,
            status: v.status,
            suspendedAt: v.suspended_at ?? undefined,
            updatedAt: v.updated_at ?? undefined,
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
