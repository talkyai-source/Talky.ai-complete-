// Canonical FastAPI AuditLogResponse/SecurityEventResponse fixtures. These are
// synthetic HTTP rows, not records read from a live database.
export const EVENT_TIME = "2026-10-21T23:59:59.999999+00:00";
export const ACTOR_ID = "20000000-0000-4000-8000-000000000001";
export const SUBJECT_ID = "20000000-0000-4000-8000-000000000002";
export const TENANT_ID = "20000000-0000-4000-8000-000000000003";
export const PARTNER_ID = "20000000-0000-4000-8000-000000000004";
export const SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"] as const;
export const SECURITY_STATUSES = ["open", "investigating", "resolved", "false_positive", "escalated"] as const;
export const eventId = (index: number) => `30000000-0000-4000-8000-${String(index).padStart(12, "0")}`;
export const auditRow = (index = 1, extra: Record<string, unknown> = {}) => ({
    event_id: eventId(index), event_time: EVENT_TIME, event_type: "user_updated", event_category: "USER_MANAGEMENT",
    severity: "INFO", actor_id: ACTOR_ID, actor_type: "user", tenant_id: TENANT_ID,
    resource_type: "user", resource_id: SUBJECT_ID, action: `owned_audit_action_${index}`,
    description: "Saved role operation", ip_address: null, user_agent: null, session_id: null,
    before_state: null, after_state: null, metadata: { new_role: "readonly" }, compliance_tags: [], ...extra,
});
export const securityRow = (index = 1, extra: Record<string, unknown> = {}) => ({
    event_id: eventId(index), created_at: EVENT_TIME, event_type: "failed_login_wrong_password",
    severity: "CRITICAL", status: "open", tenant_id: TENANT_ID, user_id: SUBJECT_ID,
    title: `Owned security alert ${index}`, description: "Saved affected-account signal", detection_source: "auth",
    evidence: { attempts: 3 }, assigned_to: null, resolved_at: null, resolved_by: null,
    resolution_notes: null, auto_action_taken: null, sla_deadline: null, ...extra,
});
