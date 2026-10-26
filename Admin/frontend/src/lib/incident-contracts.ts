import type { IncidentItem, IncidentsResponse, IncidentTransitionReceipt } from './api';

const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === 'string';
const nullableText = (value: unknown) => value === null || text(value);
const count = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
const optionalText = (value: unknown) => value === undefined || nullableText(value);
const actorId = (value: unknown): value is string => text(value) && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
const timestamp = (value: unknown): value is string => text(value)
    && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/i.test(value)
    && Number.isFinite(Date.parse(value));
const receiptPair = (actor: unknown, time: unknown) => actor === null && time === null || actorId(actor) && timestamp(time);

/** Validate a saved transition, without inventing a new actor or timestamp on replay. */
export function validIncidentTransitionReceipt(value: unknown, id: string, target: 'acknowledged' | 'resolved'): value is IncidentTransitionReceipt {
    if (!record(value) || value.success !== true || value.incident_id !== id || value.status !== target
        || !text(value.message) || !value.message.trim()
        || !receiptPair(value.acknowledged_by, value.acknowledged_at)
        || !receiptPair(value.resolved_by, value.resolved_at)) return false;
    return target === 'acknowledged'
        ? value.acknowledged_at !== null && value.resolved_at === null
        : value.resolved_at !== null;
}

// Only the fields consumed by the incident views are checked here. Optional
// source metadata remains compatible with existing manually recorded incidents.
export function validIncident(value: unknown): value is IncidentItem {
    return record(value) && text(value.id) && value.id.trim().length > 0
        && text(value.title) && ['critical', 'warning', 'info'].includes(value.severity as string)
        && ['open', 'acknowledged', 'resolved'].includes(value.status as string)
        && text(value.triggered_at) && nullableText(value.description)
        && ['acknowledged_at', 'acknowledged_by', 'resolved_at', 'resolved_by'].every(key => nullableText(value[key]))
        && ['source_system', 'source_recovered_at', 'source_last_received_at'].every(key => optionalText(value[key]))
        && (value.source_status === undefined || value.source_status === null
            || value.source_status === 'firing' || value.source_status === 'recovered');
}

export function validIncidentList(value: unknown): value is IncidentsResponse {
    return record(value) && Array.isArray(value.items) && value.items.every(validIncident)
        && ['total', 'open_count', 'critical_count'].every(key => count(value[key]))
        && count(value.page) && value.page > 0
        && count(value.page_size) && value.page_size > 0;
}
