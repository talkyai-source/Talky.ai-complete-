import type { Call } from "@/lib/dashboard-api";

export const CALL_HISTORY_LEAD_TYPES = ["cold", "warm", "hot", "follow_up"] as const;

export type CallHistoryLeadType = (typeof CALL_HISTORY_LEAD_TYPES)[number];

export interface CallHistoryFormData {
    contact: string;
    interest: string;
    nextStep: string;
    completed: boolean;
}

export interface CallHistoryWorkflowEntry {
    leadType: CallHistoryLeadType;
    notes: string;
    form: CallHistoryFormData;
    updatedAt: string;
}

export type CallHistoryWorkflowMap = Record<string, CallHistoryWorkflowEntry>;

const STORAGE_PREFIX = "talklee.call-history.workflow.v1";
const MAX_SAVED_CALLS = 500;
const ACTIVE_CALL_STATUSES = new Set([
    "queued",
    "pending",
    "initiated",
    "starting",
    "dialing",
    "ringing",
    "connecting",
    "answered",
    "in_call",
    "live",
    "in_progress",
]);

export const EMPTY_CALL_HISTORY_FORM: CallHistoryFormData = {
    contact: "",
    interest: "",
    nextStep: "",
    completed: false,
};

function isLeadType(value: unknown): value is CallHistoryLeadType {
    return typeof value === "string" && CALL_HISTORY_LEAD_TYPES.includes(value as CallHistoryLeadType);
}

function cleanForm(value: unknown): CallHistoryFormData {
    if (!value || typeof value !== "object") return { ...EMPTY_CALL_HISTORY_FORM };
    const form = value as Partial<CallHistoryFormData>;
    return {
        contact: typeof form.contact === "string" ? form.contact.slice(0, 160) : "",
        interest: typeof form.interest === "string" ? form.interest.slice(0, 1000) : "",
        nextStep: typeof form.nextStep === "string" ? form.nextStep.slice(0, 1000) : "",
        completed: form.completed === true,
    };
}

function storageKey(scope: string): string {
    return `${STORAGE_PREFIX}:${encodeURIComponent(scope || "anonymous")}`;
}

export function isActiveCallStatus(status: string): boolean {
    return ACTIVE_CALL_STATUSES.has(status.trim().toLowerCase());
}

export function callHasCapturedContact(
    call: Pick<Call, "captured_phone" | "captured_email">,
): boolean {
    return Boolean(call.captured_phone?.trim() || call.captured_email?.trim());
}

const FAILED_CALL_OUTCOMES = new Set([
    "busy",
    "failed",
    "no_answer",
    "rejected",
    "timeout",
    "unavailable",
]);

// Outcomes the backend records for a call that was picked up and ran
// (app/domain/services/call_status.py CallOutcome + the goal outcomes).
const CONNECTED_CALL_OUTCOMES = new Set([
    "answered",
    "completed",
    "customer_hung_up",
    "agent_hung_up",
    "goal_achieved",
    "goal_not_achieved",
]);

export function classifyCall(call: Pick<Call, "status" | "outcome">): { answered: boolean; failed: boolean } {
    const status = call.status.trim().toLowerCase();
    const outcome = call.outcome?.trim().toLowerCase() ?? "";
    const failed =
        ["busy", "failed", "no_answer"].includes(status) ||
        FAILED_CALL_OUTCOMES.has(outcome);
    // A finished call ends with status "ended" and carries the result in
    // outcome (2026-09-28: every answered call read "0 answered" because only
    // the status was checked).
    return {
        answered: !failed && (["answered", "completed"].includes(status) || CONNECTED_CALL_OUTCOMES.has(outcome)),
        failed,
    };
}

// Country calling code -> national digit grouping, for the numbers this
// product dials most. Anything else falls back to groups of three.
const PHONE_GROUPS: Array<[string, number[]]> = [
    ["92", [3, 7]], // Pakistan mobile: +92 312 0750496
    ["44", [4, 6]], // UK: +44 7429 916656
    ["1", [3, 3, 4]], // NANP: +1 647 347 6870
    ["971", [2, 3, 4]],
    ["91", [5, 5]],
    ["61", [3, 3, 3]],
];

/** A stored contact number laid out the way people read it. Never drops digits. */
export function formatPhoneForDisplay(value: string | null | undefined): string {
    const raw = (value ?? "").trim();
    if (!raw.startsWith("+")) return raw;
    const digits = raw.slice(1).replace(/D/g, "");
    if (digits.length < 7) return raw;
    for (const [code, groups] of PHONE_GROUPS) {
        const national = digits.slice(code.length);
        if (digits.startsWith(code) && national.length === groups.reduce((a, b) => a + b, 0)) {
            const parts: string[] = [];
            let at = 0;
            for (const size of groups) {
                parts.push(national.slice(at, at + size));
                at += size;
            }
            return "+" + code + " " + parts.join(" ");
        }
    }
    return "+" + (digits.match(/.{1,3}/g) ?? [digits]).join(" ");
}

export interface CapturedContactPart {
    kind: "phone" | "email";
    value: string;
    /** false only when the caller said it but never confirmed the read-back. */
    confirmed: boolean;
}

/** The contacts the caller gave, each with whether they confirmed it. */
export function capturedContactParts(
    call: Pick<Call, "captured_phone" | "captured_email" | "captured_phone_confirmed" | "captured_email_confirmed">,
): CapturedContactPart[] {
    const parts: CapturedContactPart[] = [];
    const phone = call.captured_phone?.trim();
    const email = call.captured_email?.trim();
    if (phone) parts.push({ kind: "phone", value: formatPhoneForDisplay(phone), confirmed: call.captured_phone_confirmed !== false });
    if (email) parts.push({ kind: "email", value: email, confirmed: call.captured_email_confirmed !== false });
    return parts;
}

export function inferCallLeadType(
    call: Pick<Call, "lead_outcome" | "outcome" | "status" | "captured_phone" | "captured_email">,
): CallHistoryLeadType {
    // A caller who gave us a way to reach them is a hot lead, whatever the
    // summary verdict says (2026-09-25: b97ce4c5 left +923085397539 but its
    // verdict was "callback", so it never showed as hot).
    if (callHasCapturedContact(call)) return "hot";
    const verdict = call.lead_outcome?.split("|")[0].trim().toLowerCase() ?? "";
    const outcome = call.outcome?.trim().toLowerCase() ?? "";
    const status = call.status.trim().toLowerCase();
    const coldResults = ["failed", "no_answer", "busy", "rejected", "unavailable"];

    if (verdict.startsWith("callback")) return "follow_up";
    if (verdict.startsWith("qualified") || outcome === "goal_achieved") return "hot";
    if (
        verdict.startsWith("no_interest") ||
        verdict.startsWith("disqualified") ||
        coldResults.includes(outcome) ||
        coldResults.includes(status)
    ) {
        return "cold";
    }
    return "warm";
}

export function defaultCallHistoryWorkflow(
    call: Pick<Call, "lead_outcome" | "outcome" | "status" | "captured_phone" | "captured_email">,
): CallHistoryWorkflowEntry {
    return {
        leadType: inferCallLeadType(call),
        notes: "",
        form: { ...EMPTY_CALL_HISTORY_FORM },
        updatedAt: "",
    };
}

export function readCallHistoryWorkflow(scope: string): CallHistoryWorkflowMap {
    if (typeof window === "undefined") return {};
    try {
        const raw = window.localStorage.getItem(storageKey(scope));
        if (!raw) return {};
        const parsed = JSON.parse(raw) as unknown;
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};

        const cleaned: CallHistoryWorkflowMap = {};
        for (const [callId, value] of Object.entries(parsed)) {
            if (!callId || !value || typeof value !== "object") continue;
            const entry = value as Partial<CallHistoryWorkflowEntry>;
            if (!isLeadType(entry.leadType)) continue;
            cleaned[callId] = {
                leadType: entry.leadType,
                notes: typeof entry.notes === "string" ? entry.notes.slice(0, 4000) : "",
                form: cleanForm(entry.form),
                updatedAt: typeof entry.updatedAt === "string" ? entry.updatedAt : "",
            };
        }
        return cleaned;
    } catch {
        return {};
    }
}

export function writeCallHistoryWorkflow(scope: string, value: CallHistoryWorkflowMap): void {
    if (typeof window === "undefined") return;
    try {
        const entries = Object.entries(value)
            .sort(([, a], [, b]) => b.updatedAt.localeCompare(a.updatedAt))
            .slice(0, MAX_SAVED_CALLS);
        window.localStorage.setItem(storageKey(scope), JSON.stringify(Object.fromEntries(entries)));
    } catch {
        // Storage may be unavailable (private mode or a full quota). The live
        // controls remain usable for the current page session.
    }
}

export function isCallHistoryFormComplete(form: CallHistoryFormData): boolean {
    return Boolean(form.contact.trim() && form.interest.trim() && form.nextStep.trim());
}
