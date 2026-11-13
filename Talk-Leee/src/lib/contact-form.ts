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
