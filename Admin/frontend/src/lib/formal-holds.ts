/** Contracts for the existing administrator-operated formal hold workflow. */
export type HoldTargetType = 'user' | 'tenant' | 'partner';
export interface HoldTarget { type: HoldTargetType; id: string; label: string }
export type HoldType = 'ADMIN' | 'TEMPORARY' | 'BILLING' | 'ABUSE' | 'COMPLIANCE' | 'EMERGENCY';
export interface HoldRequest {
    suspension_type: HoldType; reason_category: string; reason_description: string;
    duration_hours?: number; evidence: { operator_request_id: string; operator_note?: string }; notify_user: boolean;
}
export interface FormalHoldEvent {
    suspension_id: string; target_type: HoldTargetType; target_id: string; suspension_type: HoldType;
    reason_category: string; reason_description: string; suspended_at: string; suspended_until: string | null;
    suspended_by: string | null; restored_at: string | null; is_active: boolean; audit_log_id: string | null;
    appeal_submitted_at: string | null; appeal_reason: string | null; appeal_decision: string | null;
    appeal_response: string | null; restore_reason: string | null; evidence?: Record<string, unknown> | null;
}
export interface FormalHoldStatus {
    target_type: HoldTargetType; target_id: string; status: string; is_suspended: boolean;
    effective_status?: string | null; remaining_hold_ids: string[];
    active_suspension: FormalHoldEvent | null; suspension_history: FormalHoldEvent[];
    history_complete: boolean; history_limit: number;
}
export interface HoldDimension {
    key: 'credentials' | 'assistant_runtimes' | 'telephony';
    status: 'confirmed' | 'pending' | 'unverified' | 'exempt_by_policy' | 'not_applicable';
    coverage: 'complete' | 'incomplete' | 'unknown';
    counts: { captured: number; observed: number; confirmed: number; pending: number; exempt: number; unknown: number };
    reasons: string[];
}
export interface HoldPropagation {
    version: 1; suspension_id: string; target_type: HoldTargetType; target_id: string;
    event_state: 'active' | 'restored'; policy_cutoff: string; observed_at_db: string;
    original_audit_event_id: string | null;
    current_authority: { status: 'restricted' | 'allowed' | 'unknown'; remaining_hold_ids: string[] };
    propagation_status: 'confirmed' | 'pending' | 'unverified'; dimensions: HoldDimension[];
    reconciliation_reasons: string[];
}
export interface HoldReceipt {
    suspension_id: string; audit_event_id: string; target_type?: HoldTargetType; target_id?: string;
    status?: string; changed?: boolean; restored?: boolean; appeal_submitted?: boolean; decision?: string;
    effective_status?: string | null; remaining_hold_ids?: string[]; restoration_warnings?: string[];
    propagated_to?: string[]; propagation_failed?: string[]; propagation_pending?: string[];
    hint_status?: string; notification_delivery_status?: string;
}
export type HoldIntent = {
    kind: 'hold'; target: HoldTarget; request: HoldRequest; immediate: boolean; actorId: string; previousIds: string[];
} | { kind: 'restore'; target: HoldTarget; eventId: string; reason: string }
  | { kind: 'appeal'; target: HoldTarget; eventId: string; reason: string }
  | { kind: 'review'; target: HoldTarget; eventId: string; reason: string; decision: 'granted' | 'denied' };

const object = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
export const isHoldUuid = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every((v) => typeof v === 'string');
const date = (value: unknown) => typeof value === 'string' && Number.isFinite(Date.parse(value));
function matchesEvent(value: unknown, target: HoldTarget): value is FormalHoldEvent {
    return object(value) && isHoldUuid(value.suspension_id) && value.target_type === target.type && value.target_id === target.id
        && typeof value.is_active === 'boolean' && typeof value.reason_description === 'string'
        && typeof value.reason_category === 'string' && date(value.suspended_at)
        && ['suspended_until', 'suspended_by', 'restored_at', 'audit_log_id', 'appeal_submitted_at', 'appeal_reason', 'appeal_decision', 'appeal_response', 'restore_reason'].every((key) => value[key] === null || typeof value[key] === 'string')
        && (value.evidence === undefined || value.evidence === null || object(value.evidence))
        && ['ADMIN', 'TEMPORARY', 'BILLING', 'ABUSE', 'COMPLIANCE', 'EMERGENCY'].includes(String(value.suspension_type));
}
export function matchesHoldStatus(value: unknown, target: HoldTarget): value is FormalHoldStatus {
    return object(value) && value.target_type === target.type && value.target_id === target.id && typeof value.is_suspended === 'boolean'
        && (value.effective_status === undefined || value.effective_status === null || typeof value.effective_status === 'string')
        && typeof value.status === 'string' && typeof value.history_complete === 'boolean'
        && Number.isInteger(value.history_limit) && Number(value.history_limit) > 0 && strings(value.remaining_hold_ids)
        && (value.active_suspension === null || matchesEvent(value.active_suspension, target))
        && Array.isArray(value.suspension_history) && value.suspension_history.every((e) => matchesEvent(e, target));
}
export function matchesHoldPropagation(value: unknown, target: HoldTarget, eventId: string): value is HoldPropagation {
    if (!object(value) || value.version !== 1 || value.suspension_id !== eventId || value.target_id !== target.id || value.target_type !== target.type
        || !date(value.observed_at_db) || !date(value.policy_cutoff) || !['active', 'restored'].includes(String(value.event_state))
        || !['confirmed', 'pending', 'unverified'].includes(String(value.propagation_status)) || !strings(value.reconciliation_reasons)
        || !object(value.current_authority) || !['restricted', 'allowed', 'unknown'].includes(String(value.current_authority.status))
        || !strings(value.current_authority.remaining_hold_ids) || !Array.isArray(value.dimensions) || value.dimensions.length !== 3) return false;
    const keys = new Set<string>();
    for (const item of value.dimensions) {
        if (!object(item) || !['credentials', 'assistant_runtimes', 'telephony'].includes(String(item.key)) || keys.has(String(item.key))
            || !['confirmed', 'pending', 'unverified', 'exempt_by_policy', 'not_applicable'].includes(String(item.status))
            || !['complete', 'incomplete', 'unknown'].includes(String(item.coverage)) || !strings(item.reasons) || !object(item.counts)
            || ['captured', 'observed', 'confirmed', 'pending', 'exempt', 'unknown'].some((key) => !Number.isInteger(item.counts && (item.counts as Record<string, unknown>)[key]) || Number((item.counts as Record<string, unknown>)[key]) < 0)) return false;
        keys.add(String(item.key));
        if (['confirmed', 'exempt_by_policy', 'not_applicable'].includes(String(item.status))
            && (item.coverage !== 'complete' || item.counts.pending || item.counts.unknown)) return false;
    }
    const complete = value.dimensions.every((item) => item.coverage === 'complete'
        && ['confirmed', 'exempt_by_policy', 'not_applicable'].includes(item.status));
    const expected = complete ? 'confirmed' : value.dimensions.some((item) => item.status === 'pending') ? 'pending' : 'unverified';
    return value.propagation_status === expected;
}
export function matchesHoldReceipt(value: unknown, intent: HoldIntent): value is HoldReceipt {
    if (!object(value) || !isHoldUuid(value.suspension_id) || !isHoldUuid(value.audit_event_id)) return false;
    if (value.effective_status !== undefined && value.effective_status !== null && typeof value.effective_status !== 'string') return false;
    if (value.restoration_warnings !== undefined && !strings(value.restoration_warnings)) return false;
    if (value.target_id !== undefined && value.target_id !== intent.target.id) return false;
    if (value.target_type !== undefined && value.target_type !== intent.target.type) return false;
    if (intent.kind === 'hold') return value.target_id === intent.target.id && value.target_type === intent.target.type && value.status === 'suspended';
    if (value.suspension_id !== intent.eventId) return false;
    if (intent.kind === 'restore') return value.restored === true;
    if (intent.kind === 'appeal') return value.appeal_submitted === true && value.status === 'pending_review';
    return value.decision === intent.decision && (intent.decision === 'denied' || value.restored === true);
}

/** Observe a matching saved state without inventing a receipt for the lost request. */
export function reconcileHoldIntent(status: FormalHoldStatus, intent: HoldIntent): FormalHoldEvent | null {
    const events = [status.active_suspension, ...status.suspension_history].filter((e): e is FormalHoldEvent => e !== null);
    if (intent.kind === 'hold') {
        const candidates = [...new Map(events.map((e) => [e.suspension_id, e])).values()].filter((e) =>
            !intent.previousIds.includes(e.suspension_id) && (e.is_active || date(e.restored_at)) && isHoldUuid(e.audit_log_id)
            && isHoldUuid(intent.request.evidence.operator_request_id)
            && e.evidence?.operator_request_id === intent.request.evidence.operator_request_id
            && e.evidence?.operator_note === intent.request.evidence.operator_note
            && (intent.target.type !== 'tenant' || (object(e.evidence?._formal_account_hold)
                && e.evidence._formal_account_hold.version === 1 && e.evidence._formal_account_hold.immediate === intent.immediate))
            && e.suspended_by === intent.actorId && e.suspension_type === intent.request.suspension_type
            && (intent.request.duration_hours === undefined ? e.suspended_until === null
                : e.suspended_until !== null && Date.parse(e.suspended_until) - Date.parse(e.suspended_at) === intent.request.duration_hours * 3_600_000)
            && e.reason_category === intent.request.reason_category && e.reason_description === intent.request.reason_description);
        return candidates.length === 1 ? candidates[0] : null;
    }
    const event = events.find((e) => e.suspension_id === intent.eventId);
    if (!event || !isHoldUuid(event.audit_log_id)) return null;
    if (intent.kind === 'restore') return !event.is_active && date(event.restored_at) ? event : null;
    if (intent.kind === 'appeal') return event.appeal_reason === intent.reason && date(event.appeal_submitted_at) ? event : null;
    return event.appeal_decision === intent.decision && event.appeal_response === intent.reason
        && (intent.decision === 'denied' || (!event.is_active && date(event.restored_at))) ? event : null;
}
