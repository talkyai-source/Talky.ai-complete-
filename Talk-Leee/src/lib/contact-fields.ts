import type { ContactMutation } from "@/lib/dashboard-api";
import { optionalE164, requiredE164 } from "@/lib/contact-form";
import type { CountrySelection } from "@/lib/phone-number";

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

/** Keys that hold a phone number: sent only as the shared parser's E.164. */
const NUMBER_KEYS = new Set(["mobile_number", "business_number"]);

/** Strip empty optional strings so an untouched field never overwrites a stored value with "".
 *  Numbers go through the shared phone parser (E.164 or a thrown error). */
export function contactPayload(form: ContactMutation, country: CountrySelection = ""): ContactMutation {
    const out: Record<string, unknown> = { phone_number: requiredE164(form.phone_number, country) };
    for (const [key, value] of Object.entries(form)) {
        if (key === "phone_number") continue;
        if (typeof value === "boolean") {
            out[key] = value;
        } else if (typeof value === "string" && value.trim()) {
            out[key] = NUMBER_KEYS.has(key) ? optionalE164(value, country) : value.trim();
        }
    }
    return out as unknown as ContactMutation;
}

/** Payload for EDITING a contact. Unlike {@link contactPayload} a field the
 *  operator cleared is sent explicitly (null; "" for names / mobile, which the
 *  API treats as "remove"), because the PATCH only changes keys it receives and
 *  omitting them leaves the old value stored while the UI says "Saved". */
export function contactEditPayload(form: ContactMutation, country: CountrySelection = ""): ContactMutation {
    const out: Record<string, unknown> = { phone_number: requiredE164(form.phone_number, country) };
    const keepEmptyString = new Set(["first_name", "last_name", "mobile_number"]);
    for (const [key, value] of Object.entries(form)) {
        if (key === "phone_number") continue;
        if (typeof value === "boolean") {
            out[key] = value;
        } else if (typeof value === "string") {
            const trimmed = NUMBER_KEYS.has(key) ? optionalE164(value, country) : value.trim();
            out[key] = trimmed || (keepEmptyString.has(key) ? "" : null);
        }
    }
    return out as unknown as ContactMutation;
}

/** Page size the contacts API accepts at most. */
export const CONTACTS_PAGE_SIZE = 100;
/** Safety cap so a huge campaign cannot loop the browser forever. */
export const CONTACTS_MAX_PAGES = 50;

/** Load every page of a paged list (not just page 1) so client-side search and
 *  the Leads filter cover all rows. Stops at `total`, an empty page, or the cap. */
export async function fetchAllPages<T>(
    fetchPage: (page: number, pageSize: number) => Promise<{ items: T[]; total: number }>,
    pageSize: number = CONTACTS_PAGE_SIZE,
    maxPages: number = CONTACTS_MAX_PAGES,
): Promise<{ items: T[]; total: number }> {
    const first = await fetchPage(1, pageSize);
    const items = [...first.items];
    let total = first.total;
    for (let page = 2; page <= maxPages && items.length < total; page++) {
        const next = await fetchPage(page, pageSize);
        if (next.items.length === 0) break;
        items.push(...next.items);
        total = next.total;
    }
    return { items, total };
}
