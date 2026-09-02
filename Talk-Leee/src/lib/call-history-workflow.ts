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

export function inferCallLeadType(
    call: Pick<Call, "lead_outcome" | "outcome" | "status">,
): CallHistoryLeadType {
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
    call: Pick<Call, "lead_outcome" | "outcome" | "status">,
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
