import type { PersonaType } from "@/lib/dashboard-api";

/**
 * Frontend mirror of the backend persona registry. Drives the
 * campaign-create form: which slots to ask for, validation, and
 * preview. Keep REQUIRED lists in sync with
 * `backend/app/services/scripts/prompts/personas/*.py`.
 */

export type SlotKind = "text" | "textarea" | "list" | "kv-list";

export interface SlotDef {
    key: string;
    label: string;
    placeholder?: string;
    kind: SlotKind;
    required?: boolean;
    help?: string;
}

export interface PersonaSpec {
    value: PersonaType;
    title: string;
    summary: string;
    slots: SlotDef[];
}

export const PERSONAS: PersonaSpec[] = [
    {
        value: "lead_gen",
        title: "Lead Generation",
        summary: "Outbound calls — qualify leads and book consultations.",
        slots: [
            { key: "industry", label: "Industry", placeholder: "e.g. roofing", kind: "text", required: true },
            { key: "services_description", label: "Services", placeholder: "What the company does, one sentence", kind: "textarea", required: true },
            { key: "pricing_info", label: "Pricing", placeholder: "e.g. Zero upfront — you only pay when live", kind: "textarea", required: true },
            { key: "coverage_area", label: "Coverage area", placeholder: "e.g. greater Phoenix within 50 miles", kind: "text", required: true },
            { key: "company_differentiator", label: "What makes you different", placeholder: "The one reason to pick you", kind: "textarea", required: true },
            { key: "value_proposition", label: "Value for the caller", placeholder: "What the caller personally gets", kind: "textarea", required: true },
            { key: "call_reason", label: "Reason for calling", placeholder: "Why are we ringing this lead?", kind: "textarea", required: true },
            { key: "qualification_questions", label: "Qualification questions", help: "One per line", kind: "list", required: true },
            { key: "disqualifying_answers", label: "Disqualifying answers", help: "Comma-separated or one per line", kind: "list", required: true },
            { key: "calendar_booking_type", label: "What you book", placeholder: "e.g. a free home assessment", kind: "text", required: true },
        ],
    },
    {
        value: "customer_support",
        title: "Customer Support",
        summary: "Inbound — resolve issues, handle escalations, book callbacks.",
        slots: [
            { key: "business_hours", label: "Business hours", placeholder: "e.g. Mon–Fri 9am–6pm EST", kind: "text", required: true },
            { key: "website", label: "Website", placeholder: "company.com", kind: "text", required: true },
            { key: "support_email", label: "Support email", placeholder: "support@company.com", kind: "text", required: true },
            { key: "refund_policy", label: "Refund policy", kind: "textarea", required: true },
            { key: "cancellation_policy", label: "Cancellation policy", kind: "textarea", required: true },
            { key: "complaint_policy", label: "Complaint policy", kind: "textarea", required: true },
            { key: "support_topics", label: "Topics you handle", help: "Comma-separated or one per line", kind: "list", required: true },
            { key: "common_issues", label: "Common issues & solutions", help: "One per line, format: Issue | Solution", kind: "kv-list", required: true },
            { key: "escalate_triggers", label: "Escalation triggers", help: "One per line", kind: "list", required: true },
            { key: "escalate_to", label: "Escalate to", placeholder: "e.g. a senior support specialist", kind: "text", required: true },
            { key: "escalation_wait_time", label: "Escalation wait time", placeholder: "e.g. 30 minutes", kind: "text", required: true },
        ],
    },
    {
        value: "receptionist",
        title: "AI Receptionist",
        summary: "Inbound — answer, route, book appointments, take messages.",
        slots: [
            { key: "business_type", label: "Business type", placeholder: "e.g. dental practice", kind: "text", required: true },
            { key: "business_address", label: "Address", kind: "text", required: true },
            { key: "business_phone", label: "Phone", placeholder: "For giving out to callers", kind: "text", required: true },
            { key: "business_email", label: "Email", kind: "text", required: true },
            { key: "website", label: "Website", kind: "text", required: true },
            { key: "opening_hours", label: "Opening hours", placeholder: "e.g. Mon–Fri 9–6; Sat 10–2", kind: "textarea", required: true },
            { key: "services", label: "Services", help: "One per line or comma-separated", kind: "list", required: true },
            { key: "emergency_protocol", label: "Emergency protocol", kind: "textarea", required: true },
            { key: "new_patient_info_needed", label: "Intake fields (new caller)", help: "One per line", kind: "list", required: true },
        ],
    },
];

/** Parse a kv-list textarea (lines of "key | value") into the shape the
 *  backend's customer_support persona expects for common_issues. */
export function parseKvList(raw: string): Array<{ issue: string; solution: string }> {
    return raw
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean)
        .map((line) => {
            const [issue, ...rest] = line.split("|");
            return {
                issue: (issue || "").trim(),
                solution: rest.join("|").trim(),
            };
        })
        .filter((item) => item.issue);
}

/** Parse a list textarea (newlines or commas) into a plain string[]. */
export function parseList(raw: string): string[] {
    return raw
        .split(/[\n,]/)
        .map((s) => s.trim())
        .filter(Boolean);
}

/** Parse agent-name input (comma or newline separated, max 3). */
export function parseAgentNames(raw: string): string[] {
    return parseList(raw).slice(0, 3);
}
