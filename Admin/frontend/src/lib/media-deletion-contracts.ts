import type {
    AdminFeedbackItem, AdminMediaDeletionItem, AdminMediaDeletionList, AdminMediaDeletionParams,
    AdminMediaDeletionReceipt, AdminMediaResourceType,
} from './api';

export interface MediaDeletionTarget {
    resource_type: AdminMediaResourceType;
    resource_id: string;
    call_id: string;
    tenant_id: string;
}

export interface MediaDeletionNotice {
    target: MediaDeletionTarget;
    state: 'pending' | 'unconfirmed' | 'completed' | 'absent';
    receipt?: AdminMediaDeletionReceipt;
    saved?: AdminMediaDeletionItem;
    message?: string;
}

const object = (value: unknown): value is Record<string, unknown> => Boolean(value) && typeof value === 'object' && !Array.isArray(value);
const uuid = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
const awareTime = (value: unknown): value is string => typeof value === 'string'
    && /T.*(?:Z|[+-]\d{2}:\d{2})$/i.test(value) && Number.isFinite(Date.parse(value));
const optionalTime = (value: unknown) => value === null || awareTime(value);
const stages = new Set(['intent_committed', 'object_deleted', 'failed', 'completed']);

export function mediaDeletionTarget(resource_type: AdminMediaResourceType, item: { id: string; call_id: string; tenant_id: string }): MediaDeletionTarget {
    return { resource_type, resource_id: item.id, call_id: item.call_id, tenant_id: item.tenant_id };
}

export function matchesMediaDeletionReceipt(value: unknown, target: MediaDeletionTarget): value is AdminMediaDeletionReceipt {
    return object(value) && value.resource_type === target.resource_type && value.resource_id === target.resource_id
        && uuid(value.resource_id) && typeof value.detail === 'string' && Boolean(value.detail.trim())
        && (value.outcome === 'completed' ? uuid(value.deletion_intent_id)
            : value.outcome === 'absent' && value.deletion_intent_id === null);
}

export function validMediaDeletion(value: unknown, filters: AdminMediaDeletionParams = {}): value is AdminMediaDeletionItem {
    if (!object(value) || !['recording', 'call_feedback'].includes(String(value.resource_type))
        || !uuid(value.id) || !uuid(value.resource_id) || !uuid(value.call_id) || !uuid(value.tenant_id) || !uuid(value.original_actor_id)
        || typeof value.reason !== 'string' || !value.reason.trim() || !stages.has(String(value.status))
        || !Number.isSafeInteger(value.attempt_count) || Number(value.attempt_count) < 1
        || !awareTime(value.created_at) || !awareTime(value.updated_at)
        || !optionalTime(value.object_deleted_at) || !optionalTime(value.completed_at)) return false;
    return (!filters.resource_type || value.resource_type === filters.resource_type)
        && (!filters.resource_id || value.resource_id === filters.resource_id)
        && (!filters.tenant_id || value.tenant_id === filters.tenant_id)
        && (!filters.call_id || value.call_id === filters.call_id)
        && (!filters.status || filters.status === 'all' || value.status === filters.status);
}

export function validMediaDeletionList(value: unknown, filters: AdminMediaDeletionParams, limit: number): value is AdminMediaDeletionList {
    return object(value) && Array.isArray(value.items) && value.items.length <= limit
        && value.items.every(item => validMediaDeletion(item, filters))
        && new Set(value.items.map(item => item.id)).size === value.items.length
        && typeof value.has_more === 'boolean'
        && (value.has_more ? value.items.length > 0 && typeof value.next_cursor === 'string'
            && value.next_cursor.length > 0 && value.next_cursor.length <= 1024
            : value.next_cursor === null);
}

export function sameMediaDeletionTarget(item: AdminMediaDeletionItem, target: MediaDeletionTarget): boolean {
    return item.resource_type === target.resource_type && item.resource_id === target.resource_id
        && item.tenant_id === target.tenant_id && item.call_id === target.call_id;
}

export function recoveredMediaDeletion(notice: MediaDeletionNotice, saved: AdminMediaDeletionItem): MediaDeletionNotice {
    if (!sameMediaDeletionTarget(saved, notice.target) || notice.state === 'pending') return notice;
    const knownIntent = notice.receipt?.deletion_intent_id ?? notice.saved?.id;
    if (knownIntent && knownIntent !== saved.id) return notice;
    if (notice.state === 'completed' && saved.status !== 'completed') return notice;
    return { ...notice, saved, state: saved.status === 'completed' ? 'completed' : 'unconfirmed', message: undefined };
}

export function matchesFeedbackRetry(value: unknown, item: Pick<AdminFeedbackItem, 'id' | 'call_id' | 'tenant_id' | 'transcription_attempts'>): value is AdminFeedbackItem {
    return object(value) && value.id === item.id && value.call_id === item.call_id && value.tenant_id === item.tenant_id
        && ['pending', 'done', 'failed'].includes(String(value.transcript_status))
        && Number.isSafeInteger(value.transcription_attempts) && Number(value.transcription_attempts) >= item.transcription_attempts
        && (value.transcript === null || typeof value.transcript === 'string')
        && (value.transcript_error === null || typeof value.transcript_error === 'string');
}
