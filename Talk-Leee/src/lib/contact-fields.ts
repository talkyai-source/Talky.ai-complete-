import type { ContactMutation } from "@/lib/dashboard-api";

/** Optional contact details offered by the "More details" dropdown on the
 *  Add/Edit contact form. Keys are ContactMutation fields the backend accepts. */
export type OptionalContactFieldKey =
    | "company_name"
    | "job_title"
    | "mobile_number"
    | "business_number"
    | "best_time_to_call"
    | "timezone"
    | "preferred_contact_method"
    | "calling_notes";

export const OPTIONAL_CONTACT_FIELDS: ReadonlyArray<{
    key: OptionalContactFieldKey;
    label: string;
    placeholder?: string;
    maxLength?: number;
    options?: readonly string[];
}> = [
    { key: "company_name", label: "Company", placeholder: "Acme Ltd", maxLength: 255 },
    { key: "job_title", label: "Job title", placeholder: "Owner", maxLength: 255 },
    { key: "mobile_number", label: "Mobile number", placeholder: "+44 7700 900123", maxLength: 32 },
    { key: "business_number", label: "Business number", placeholder: "+44 20 7946 0000", maxLength: 32 },
    { key: "best_time_to_call", label: "Best time to call", placeholder: "Weekdays 2–5 pm", maxLength: 64 },
    { key: "timezone", label: "Timezone", placeholder: "Europe/London", maxLength: 64 },
    { key: "preferred_contact_method", label: "Preferred contact method", options: ["phone", "mobile", "email", "sms"] },
    { key: "calling_notes", label: "Notes for the agent", placeholder: "Asked for a callback about card fees", maxLength: 4000 },
];

/** Strip empty optional strings so an untouched field never overwrites a stored value with "". */
export function contactPayload(form: ContactMutation): ContactMutation {
    const out: Record<string, unknown> = { phone_number: form.phone_number.trim() };
    for (const [key, value] of Object.entries(form)) {
        if (key === "phone_number") continue;
        if (typeof value === "boolean") {
            out[key] = value;
        } else if (typeof value === "string" && value.trim()) {
            out[key] = value.trim();
        }
    }
    return out as unknown as ContactMutation;
}
