// Shared call/lead status → colour, so GREEN/RED mean the same thing across
// the whole app (call history, call detail, contacts, live panel).
//   GREEN  = success / lead / goal achieved / answered / qualified
//   RED    = failure / no-answer / rejected / disqualified
//   BLUE   = in progress
//   AMBER  = pending / ringing / needs follow-up
//   MUTED  = neutral / unknown

export type StatusTone = "green" | "red" | "blue" | "amber" | "muted";

const TONE_PILL: Record<StatusTone, string> = {
    green: "bg-background text-emerald-800 border border-emerald-700/50 dark:text-emerald-300 dark:border-emerald-400/50",
    red: "bg-background text-red-800 border border-red-700/50 dark:text-red-300 dark:border-red-400/50",
    blue: "bg-background text-blue-800 border border-blue-700/50 dark:text-blue-300 dark:border-blue-400/50",
    amber: "bg-background text-amber-800 border border-amber-700/50 dark:text-amber-300 dark:border-amber-400/50",
    muted: "bg-background text-muted-foreground border border-border",
};

const GREEN = new Set([
    "answered", "completed", "goal_achieved", "qualified",
    "customer_hung_up", "agent_hung_up", "in_call",
]);
const RED = new Set([
    "failed", "no_answer", "busy", "rejected", "unreachable",
    "network_failure", "goal_not_achieved", "disqualified", "no_interest",
]);
const BLUE = new Set(["in_progress", "dialing", "initiated"]);
// `termination_pending` is amber, not red: the backend writes it as the fence a
// termination request takes BEFORE the provider has proved the channel gone,
// and documents it as deliberately non-terminal — it releases no billing, quota
// or concurrency. Grading it as a failure would claim an outcome the server has
// not reached yet.
//   backend/app/domain/services/call_status.py:97 — _TERMINATION_PENDING_STATUS
//   backend/app/domain/services/telephony/termination.py:262-270
// `reconciliation_required` is a transfer leg the server deliberately held
// rather than settled — its billing stays `held` pending adjudication
// (backend/app/domain/services/telephony/inbound_transfer.py:1544-1556). That
// is "needs attention", not a failure, so it is amber rather than red.
const AMBER = new Set([
    "ringing", "queued", "pending", "voicemail", "callback",
    "termination_pending", "reconciliation_required",
]);

/** Coarse tone for a call status OR a call outcome OR a lead result string. */
export function toneFor(value?: string | null): StatusTone {
    const v = (value || "").trim().toLowerCase();
    if (!v) return "muted";
    if (GREEN.has(v)) return "green";
    if (RED.has(v)) return "red";
    if (BLUE.has(v)) return "blue";
    if (AMBER.has(v)) return "amber";
    return "muted";
}

/** Tailwind classes for a status/outcome pill — drop-in for the old getStatusStyle. */
export function statusPillClass(value?: string | null): string {
    return TONE_PILL[toneFor(value)];
}

// Call `status` has no published enum (OQ-CH-03, backend-owned), so this maps
// only the declared values whose raw form would read as a leaked internal
// token, and returns everything else verbatim. A server status this file has
// never heard of must still reach the operator unchanged — never hidden,
// never guessed at, never title-cased into something the server did not say.
//   backend/app/domain/services/call_status.py:97 — _TERMINATION_PENDING_STATUS
const STATUS_LABELS: Record<string, string> = {
    termination_pending: "Ending",
};

/**
 * Human label for a call status. Declared values are mapped; anything else —
 * including a status added to the backend after this map was written — is
 * returned exactly as the server sent it.
 */
export function callStatusLabel(value?: string | null): string {
    const raw = (value || "").trim();
    const key = raw.toLowerCase();
    // Own-property check, not a bare lookup: a plain object also answers for
    // inherited members, so `status: "constructor"` would otherwise render a
    // function instead of the word the server sent.
    return Object.hasOwn(STATUS_LABELS, key) ? STATUS_LABELS[key] : raw;
}

// `call_legs.status` is an unconstrained varchar in the schema
// (backend/database/schema/baseline_2026-06-02.sql:521), so this maps the
// values the transfer code actually writes and shows anything else raw:
//   completed / failed                — backend/app/domain/services/telephony/inbound_transfer.py:1408
//   reconciliation_required           — backend/app/domain/services/telephony/inbound_transfer.py:1544-1556
//   initiated / ringing / answered    — backend/app/domain/services/telephony/termination.py:17-24
const TRANSFER_LEG_STATUS_LABELS: Record<string, string> = {
    initiated: "Dialing",
    ringing: "Ringing",
    answered: "Answered",
    completed: "Completed",
    failed: "Failed",
    reconciliation_required: "Awaiting reconciliation",
};

/**
 * Human label for one transfer leg's status. Declared values are mapped;
 * anything else is returned exactly as the server sent it, so a status this
 * map has not seen is surfaced rather than hidden behind a guess.
 */
export function transferLegStatusLabel(value?: string | null): string {
    const raw = (value || "").trim();
    const key = raw.toLowerCase();
    return Object.hasOwn(TRANSFER_LEG_STATUS_LABELS, key) ? TRANSFER_LEG_STATUS_LABELS[key] : raw;
}

// `calls.billing_status` is closed by a database CHECK constraint, so unlike
// `status` above this vocabulary is fully declared and can be mapped in full:
//   CHECK (billing_status IN ('none','reserved','held','finalized','released','reversed'))
//   backend/Alembic/versions/0022_inbound_calling_foundation.py:706
// The wording states what the server has actually done with the money, and
// says nothing it has not: `reserved` is capacity held before answer, not a
// charge, and `held` is a settlement the server deliberately did NOT make.
//   backend/app/domain/services/telephony/inbound_admission.py:1169 — 'reserved'
//   backend/app/domain/services/telephony/inbound_admission.py:1571 — finalized/released
const BILLING_STATUS_LABELS: Record<string, string> = {
    none: "Not billed",
    reserved: "Reserved",
    held: "Held for review",
    finalized: "Settled",
    released: "Released",
    reversed: "Reversed",
};

/**
 * Human label for a call's billing status. Declared values are mapped; a value
 * this map has not seen — including one added to the CHECK constraint after
 * this was written — is returned exactly as the server sent it.
 */
export function billingStatusLabel(value?: string | null): string {
    const raw = (value || "").trim();
    const key = raw.toLowerCase();
    return Object.hasOwn(BILLING_STATUS_LABELS, key) ? BILLING_STATUS_LABELS[key] : raw;
}

// `calls.billing_hold_reason` is likewise closed by a CHECK constraint, and is
// only ever non-NULL for an inbound call whose billing_status is 'held':
//   CHECK (billing_hold_reason IS NULL OR (direction='inbound' AND
//          billing_status='held' AND billing_hold_reason IN (...)))
//   backend/Alembic/versions/0032_inbound_billing_hold.py:79-88
// Each reason names why settlement was withheld, because that is the fact the
// operator needs — not a reassurance that it will settle later.
//   settlement_switch_disabled  — backend/app/domain/services/telephony/inbound_admission.py:1409
//   usage_exceeded_reservation  — backend/app/domain/services/telephony/inbound_admission.py:1499
//   provider_answer_ambiguous   — backend/app/domain/services/telephony/inbound_admission.py:67
const BILLING_HOLD_REASON_LABELS: Record<string, string> = {
    settlement_switch_disabled: "Settlement is switched off platform-wide",
    usage_exceeded_reservation: "Call ran longer than the minutes reserved for it",
    provider_answer_ambiguous: "Carrier records needed to confirm the call was answered",
};

/**
 * Human label for the reason a call's billing was held. Declared values are
 * mapped; anything else is returned exactly as the server sent it.
 */
export function billingHoldReasonLabel(value?: string | null): string {
    const raw = (value || "").trim();
    const key = raw.toLowerCase();
    return Object.hasOwn(BILLING_HOLD_REASON_LABELS, key) ? BILLING_HOLD_REASON_LABELS[key] : raw;
}
