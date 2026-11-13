// The app's labels for the server's inbound reason codes. Shown by the
// rejected-calls panel and by the readiness checklist's blocker rows.
export const REASON_LABELS: Record<string, string> = {
    after_hours_closed: "After hours",
    unknown_did: "Unknown number",
    tenant_conflict: "Number routing conflict",
    did_not_verified: "Number not verified",
    trunk_not_ready: "Phone trunk unavailable",
    concurrency_policy_missing: "Capacity policy unavailable",
    admission_timeout: "Admission timed out",
    max_active_calls_reached: "All lines in use",
    subscription_inactive: "Subscription inactive",
    tenant_inbound_disabled: "Inbound disabled",
    insufficient_minutes: "Insufficient minutes",
};
