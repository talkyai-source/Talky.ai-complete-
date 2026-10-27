// Synthetic protocol fixtures only. They do not substitute for backend atomicity tests.
export const operationId = '50000000-0000-4000-8000-000000000001';
export const auditId = '30000000-0000-4000-8000-000000000001';
export const userId = '10000000-0000-4000-8000-000000000001';
export const tenantId = '20000000-0000-4000-8000-000000000001';
export const reviewToken = 'v1:' + '1'.repeat(64);
export const nextToken = 'v1:' + '2'.repeat(64);
export const membershipToken = 'pm1:' + '3'.repeat(64);
export const prepared = (body, id = operationId) => ({ operation_id: id, kind: body.kind, target_user_id: body.target_user_id,
    intent: structuredClone(body.intent), state: 'prepared', prepared_at: '2026-10-22T12:00:00+00:00', expires_at: '2026-10-22T12:15:00+00:00',
    dispatch_eligible: true, terminal_audit_event_id: null, receipt: null });
export const terminal = (view, receipt = null) => ({ ...view, state: receipt ? 'committed' : 'cancelled', dispatch_eligible: false,
    terminal_audit_event_id: receipt?.audit_event_id ?? auditId, receipt });
