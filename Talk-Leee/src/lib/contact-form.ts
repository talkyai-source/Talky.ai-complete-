import type { ContactMutation } from "@/lib/dashboard-api";
import { parsePhoneNumber, type CountrySelection } from "@/lib/phone-number";

export type ContactFormState = {
    phone_number: string;
    first_name: string;
    last_name: string;
    mobile_number: string;
    business_number: string;
    email: string;
    company_name: string;
    job_title: string;
    best_time_to_call: string;
    timezone: string;
    calling_notes: string;
    preferred_contact_method: string;
    do_not_call: boolean;
};

export const EMPTY_CONTACT_FORM: ContactFormState = {
    phone_number: "",
    first_name: "",
    last_name: "",
    mobile_number: "",
    business_number: "",
    email: "",
    company_name: "",
    job_title: "",
    best_time_to_call: "",
    timezone: "",
    calling_notes: "",
    preferred_contact_method: "",
    do_not_call: false,
};

/** The E.164 value for a required number, or a thrown error carrying the
 *  shared parser's text. The form blocks Save before this runs; throwing is
 *  the guarantee that an invalid number can never reach a request. */
export function requiredE164(raw: string, country: CountrySelection): string {
    const parsed = parsePhoneNumber(raw, country);
    if (!parsed.ok) throw new Error(parsed.error);
    return parsed.e164;
}

/** Empty stays empty; anything typed must parse like the main number. */
export function optionalE164(raw: string, country: CountrySelection): string {
    if (!raw.trim()) return "";
    return requiredE164(raw, country);
}

/** Every IANA zone this browser knows; empty when the browser cannot list them
 *  (the API still checks, so an empty list only skips the early message). */
export function supportedTimeZones(): string[] {
    try {
        return Intl.supportedValuesOf("timeZone");
    } catch {
        return [];
    }
}

/** Empty is fine; anything typed must be a real IANA zone (same rule as the API). */
export function contactTimezoneError(raw: string, zones: string[] = supportedTimeZones()): string | null {
    const value = raw.trim();
    if (!value || zones.length === 0 || zones.includes(value)) return null;
    return `"${value}" is not a time zone. Pick one from the list, for example Europe/London or America/New_York.`;
}

/** The first reason an OPTIONAL field blocks Add/Save, in plain words, or null.
 *  The form shows it beside the button because these fields live inside the
 *  collapsible "More details" section. */
export function contactOptionalFieldProblem(
    form: ContactFormState,
    country: CountrySelection,
    zones: string[] = supportedTimeZones(),
): string | null {
    for (const [label, raw] of [["Mobile number", form.mobile_number], ["Business number", form.business_number]] as const) {
        if (!raw.trim()) continue;
        const parsed = parsePhoneNumber(raw, country);
        if (!parsed.ok) return `${label} is not valid: ${parsed.error}`;
    }
    const tz = contactTimezoneError(form.timezone, zones);
    return tz ? `Timezone: ${tz}` : null;
}

export function contactPayload(form: ContactFormState, country: CountrySelection = ""): ContactMutation {
    const firstName = form.first_name.trim();
    const lastName = form.last_name.trim();
    return {
        phone_number: requiredE164(form.phone_number, country),
        mobile_number: optionalE164(form.mobile_number, country),
        // No full_name: the API re-splits it on the first space and would
        // overwrite a multi-word first name. Empty names are sent as "" so an
        // edit can clear one (the API treats "" as remove).
        first_name: firstName,
        last_name: lastName,
        email: form.email.trim(),
        company_name: form.company_name.trim(),
        job_title: form.job_title.trim(),
        business_number: optionalE164(form.business_number, country),
        best_time_to_call: form.best_time_to_call.trim(),
        timezone: form.timezone.trim(),
        calling_notes: form.calling_notes.trim(),
        preferred_contact_method: form.preferred_contact_method.trim(),
        do_not_call: form.do_not_call,
    };
}
