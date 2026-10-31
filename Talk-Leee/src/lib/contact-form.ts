import type { ContactMutation } from "@/lib/dashboard-api";

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

export function contactPayload(form: ContactFormState): ContactMutation {
    const firstName = form.first_name.trim();
    const lastName = form.last_name.trim();
    const fullName = [firstName, lastName].filter(Boolean).join(" ");
    return {
        phone_number: form.phone_number.trim(),
        mobile_number: form.mobile_number.trim(),
        full_name: fullName,
        first_name: firstName || undefined,
        last_name: lastName || undefined,
        email: form.email.trim(),
        company_name: form.company_name.trim(),
        job_title: form.job_title.trim(),
        business_number: form.business_number.trim(),
        best_time_to_call: form.best_time_to_call.trim(),
        timezone: form.timezone.trim(),
        calling_notes: form.calling_notes.trim(),
        preferred_contact_method: form.preferred_contact_method.trim(),
        do_not_call: form.do_not_call,
    };
}
