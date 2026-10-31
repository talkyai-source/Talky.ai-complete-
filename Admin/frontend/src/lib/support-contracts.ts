// Shapes the Support console reads from /admin/support/* and the shared
// upload route, checked field by field against what the backend returns
// (backend/app/domain/services/support/projection.py, staff_service.py,
// article_admin.py, attachments.py). A typed fetch alone cannot protect a
// deployed page from an incomplete or incompatible JSON response.

export const SUPPORT_STATUSES = ['open', 'in_progress', 'waiting_on_customer', 'resolved', 'closed'] as const;
export type SupportStatus = typeof SUPPORT_STATUSES[number];
export const SUPPORT_PRIORITIES = ['urgent', 'high', 'normal', 'low'] as const;
export type SupportPriority = typeof SUPPORT_PRIORITIES[number];
export const SUPPORT_CATEGORIES = [
    { id: 'billing', label: 'Billing and payments' },
    { id: 'calls_campaigns', label: 'Calls and campaigns' },
    { id: 'phone_numbers', label: 'Phone numbers and caller ID' },
    { id: 'ai_agent_voices', label: 'AI agent and voices' },
    { id: 'integrations', label: 'Integrations and connectors' },
    { id: 'account_login', label: 'Account and sign-in' },
    { id: 'bug', label: 'Something is broken' },
    { id: 'feature_request', label: 'Feature request' },
    { id: 'privacy_data', label: 'Privacy and data' },
    { id: 'other', label: 'Something else' },
] as const;
export type SupportCategory = typeof SUPPORT_CATEGORIES[number]['id'];
export const SUPPORT_CATEGORY_IDS: readonly string[] = SUPPORT_CATEGORIES.map((item) => item.id);
export const SUPPORT_MAX_ATTACHMENTS = 5;

export const STATUS_LABELS: Record<SupportStatus, string> = {
    open: 'Open', in_progress: 'In progress', waiting_on_customer: 'Waiting on customer', resolved: 'Resolved', closed: 'Closed',
};
export const PRIORITY_LABELS: Record<SupportPriority, string> = { urgent: 'Urgent', high: 'High', normal: 'Normal', low: 'Low' };
export function categoryLabel(id: string): string {
    return SUPPORT_CATEGORIES.find((item) => item.id === id)?.label ?? id;
}

export interface SupportAttachmentRef { id: string; filename: string; size_bytes: number; content_type: string }
export interface StaffMessage {
    id: string; author_type: 'customer' | 'staff' | 'system' | string; author_name: string; body: string;
    attachments: SupportAttachmentRef[]; created_at: string; is_internal: boolean;
}
export interface BotMessage {
    id: string; role: 'user' | 'assistant' | string; content: string;
    sources: Array<{ slug: string; title: string; url: string }>; created_at: string;
}
export interface QueueSla { first_response_due_at: string | null; resolution_due_at: string | null; breached: boolean }
export interface StaffQueueItem {
    id: string; number: string; subject: string; status: SupportStatus; priority: SupportPriority; category: string;
    updated_at: string; created_at: string; unread: boolean;
    tenant: { id: string; name: string | null }; assignee: { id: string; name: string | null } | null; sla: QueueSla;
}
export interface StaffQueuePage { items: StaffQueueItem[]; next_cursor: string | null }
export interface TicketEvent {
    id: string; type: string; from_value: string | null; to_value: string | null;
    actor_id: string | null; actor_name: string | null; created_at: string;
}
export interface StaffTicketDetail {
    id: string; number: string; subject: string; category: string; priority: SupportPriority; status: SupportStatus;
    created_at: string; updated_at: string; version: number;
    requester: { id: string | null; name: string | null; email: string | null };
    related: { campaign_id?: string; call_id?: string };
    messages: StaffMessage[]; csat: { score: number; comment: string | null } | null; allowed_actions: string[];
    tenant: { id: string; name: string | null; plan: string | null } | null;
    assignee: { id: string; name: string | null; email: string | null } | null;
    sla: QueueSla & { first_responded_at: string | null };
    conversation: BotMessage[]; diagnostics: Record<string, unknown>; events: TicketEvent[];
}
export interface SupportArticle {
    id: string; slug: string; title: string; section: string; body_md: string; category: string;
    source: 'docs' | 'staff' | string; published: boolean; url: string; updated_by: string | null;
    created_at: string; updated_at: string;
}
export interface SupportMetrics {
    days: number; conversations: number; bot_solved_pct: number | null; tickets_created: number;
    open_by_priority: Record<SupportPriority, number>; sla_breaches: number;
    median_first_response_minutes: number | null; avg_csat: number | null;
}
export interface UploadReceipt { attachment_id: string; filename: string; content_type: string; size_bytes: number }

export interface StaffTicketPatch {
    request_id: string; version: number; status?: SupportStatus; priority?: SupportPriority;
    category?: SupportCategory; assignee_user_id?: string | null;
}
export interface StaffMessageInput { request_id: string; body: string; internal: boolean; attachment_ids: string[] }
export interface ArticleCreateInput { request_id: string; slug: string; title: string; body_md: string; category: SupportCategory; published: boolean }
export interface ArticlePatchInput { request_id: string; title?: string; body_md?: string; category?: SupportCategory; published?: boolean }

const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
const string = (value: unknown) => typeof value === 'string';
const nullableString = (value: unknown) => value === null || string(value);
const boolean = (value: unknown) => typeof value === 'boolean';
const count = (value: unknown) => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
const nullableNumber = (value: unknown) => value === null || (typeof value === 'number' && Number.isFinite(value));
const oneOf = (values: readonly string[]) => (value: unknown) => string(value) && values.includes(value);
const fields = (value: Record<string, unknown>, names: string[], check: (item: unknown) => boolean) => names.every((name) => check(value[name]));
const list = (value: unknown, check: (item: unknown) => boolean) => Array.isArray(value) && value.every(check);

const validAttachment = (value: unknown) => record(value) && fields(value, ['id', 'filename', 'content_type'], string) && count(value.size_bytes);
export function validStaffMessage(value: unknown): value is StaffMessage {
    return record(value) && fields(value, ['id', 'author_type', 'author_name', 'body', 'created_at'], string)
        && boolean(value.is_internal) && list(value.attachments, validAttachment);
}
const validBotMessage = (value: unknown) => record(value) && fields(value, ['id', 'role', 'content', 'created_at'], string)
    && list(value.sources, (source) => record(source) && fields(source, ['slug', 'title', 'url'], string));
const validSla = (value: unknown) => record(value) && fields(value, ['first_response_due_at', 'resolution_due_at'], nullableString) && boolean(value.breached);
const validAssignee = (value: unknown) => value === null || (record(value) && string(value.id) && nullableString(value.name));
const validEvent = (value: unknown) => record(value) && fields(value, ['id', 'type', 'created_at'], string)
    && fields(value, ['from_value', 'to_value', 'actor_id', 'actor_name'], nullableString);

export function validQueueItem(value: unknown): value is StaffQueueItem {
    return record(value) && fields(value, ['id', 'number', 'subject', 'category', 'updated_at', 'created_at'], string)
        && oneOf(SUPPORT_STATUSES)(value.status) && oneOf(SUPPORT_PRIORITIES)(value.priority) && boolean(value.unread)
        && record(value.tenant) && string(value.tenant.id) && nullableString(value.tenant.name)
        && validAssignee(value.assignee) && validSla(value.sla);
}
export function validQueuePage(value: unknown): value is StaffQueuePage {
    return record(value) && list(value.items, validQueueItem) && nullableString(value.next_cursor);
}
export function validTicketDetail(value: unknown): value is StaffTicketDetail {
    return record(value) && fields(value, ['id', 'number', 'subject', 'category', 'created_at', 'updated_at'], string)
        && oneOf(SUPPORT_STATUSES)(value.status) && oneOf(SUPPORT_PRIORITIES)(value.priority)
        && typeof value.version === 'number' && Number.isSafeInteger(value.version) && value.version >= 1
        && record(value.requester) && fields(value.requester, ['id', 'name', 'email'], nullableString)
        && record(value.related) && list(value.messages, validStaffMessage)
        && (value.csat === null || (record(value.csat) && count(value.csat.score) && nullableString(value.csat.comment)))
        && list(value.allowed_actions, string)
        && (value.tenant === null || (record(value.tenant) && string(value.tenant.id) && fields(value.tenant, ['name', 'plan'], nullableString)))
        && (value.assignee === null || (record(value.assignee) && string(value.assignee.id) && fields(value.assignee, ['name', 'email'], nullableString)))
        && validSla(value.sla) && nullableString((value.sla as Record<string, unknown>).first_responded_at)
        && list(value.conversation, validBotMessage) && record(value.diagnostics) && list(value.events, validEvent);
}
export function validArticle(value: unknown): value is SupportArticle {
    return record(value) && fields(value, ['id', 'slug', 'title', 'section', 'body_md', 'category', 'source', 'url', 'created_at', 'updated_at'], string)
        && boolean(value.published) && nullableString(value.updated_by);
}
export function validArticles(value: unknown): value is { items: SupportArticle[] } {
    return record(value) && list(value.items, validArticle);
}
export function validMetrics(value: unknown): value is SupportMetrics {
    return record(value) && fields(value, ['days', 'conversations', 'tickets_created', 'sla_breaches'], count)
        && fields(value, ['bot_solved_pct', 'median_first_response_minutes', 'avg_csat'], nullableNumber)
        && record(value.open_by_priority) && fields(value.open_by_priority, [...SUPPORT_PRIORITIES], count);
}
export function validUploadReceipt(value: unknown): value is UploadReceipt {
    return record(value) && fields(value, ['attachment_id', 'filename', 'content_type'], string) && count(value.size_bytes);
}

/** The backend's 409 for a stale `version`; api.ts upper-cases envelope codes. */
export function isVersionConflict(error: { code?: string; status?: number } | undefined): boolean {
    return Boolean(error && error.status === 409 && error.code === 'VERSION_CONFLICT');
}
