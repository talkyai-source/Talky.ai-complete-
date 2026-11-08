// Friendly, first-class labels for each call disposition so the history
// reads like a human's notes ("No Answer", "Voicemail") rather than raw
// enum values. Unknown values fall back to Title-Cased words.
const OUTCOME_LABELS: Record<string, string> = {
    goal_achieved: "Qualified",
    goal_not_achieved: "Disqualified",
    answered: "Answered",
    no_answer: "No Answer",
    busy: "Busy",
    voicemail: "Voicemail",
    rejected: "Rejected",
    failed: "Failed",
    timeout: "Timed Out",
    unavailable: "Unavailable",
    disconnected: "Disconnected",
};

export function humanizeOutcome(outcome?: string) {
    if (!outcome) return "--";
    const key = outcome.trim().toLowerCase();
    if (OUTCOME_LABELS[key]) return OUTCOME_LABELS[key];
    return key
        .replace(/_/g, " ")
        .replace(/\b\w/g, (c) => c.toUpperCase());
}

// The backend's definition of a failed call: "The call did not reach a live
// human" — FAILED_OUTCOMES in backend/app/domain/services/call_outcomes.py:54-64.
// A call settled with status 'failed' always carries outcome 'failed' too
// (telephony_bridge.py:1578-1579), so the outcome alone identifies it.
export const FAILED_CALL_OUTCOMES: ReadonlySet<string> = new Set([
    "no_answer", "busy", "rejected", "unreachable", "network_failure", "failed", "cancelled", "voicemail",
]);

export function isFailedCallOutcome(outcome: string | null | undefined): boolean {
    return FAILED_CALL_OUTCOMES.has((outcome ?? "").trim().toLowerCase());
}
