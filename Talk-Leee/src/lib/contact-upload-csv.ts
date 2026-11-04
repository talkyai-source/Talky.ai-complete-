/**
 * ONE builder for the CSV that the contacts page upload card and the campaign
 * Import CSV modal send to POST /contacts/campaigns/{id}/upload, so both paths
 * send the same columns. Every column the backend upload reads is listed; the
 * phone_number cell is always the E.164 value the shared parser produced.
 */
import type { ContactRow } from "@/lib/contact-csv";
import { parsePhoneNumber, type CountrySelection } from "@/lib/phone-number";

export const CONTACT_UPLOAD_COLUMNS = [
    "phone_number", "full_name", "first_name", "last_name", "email",
    "company_name", "mobile_number", "business_number", "job_title",
    "best_time_to_call", "timezone", "calling_notes",
    "preferred_contact_method", "do_not_call",
] as const;

/** The backend numbers the first data row 2 (row 1 is the header). */
export const UPLOAD_FIRST_DATA_ROW = 2;

/** Every number cell of a row, as the shared parser accepted it. */
export type RowNumbers = {
    /** E.164 of the main phone. */
    e164: string;
    /** E.164 of mobile_number, or "" when the cell was empty. */
    mobile_e164: string;
    /** E.164 of business_number, or "" when the cell was empty. */
    business_e164: string;
};

export type RowNumbersResult =
    | { ok: true; numbers: RowNumbers }
    | { ok: false; error: string };

/** The one rule for every number cell of a CSV row (EP1, EP2 and EP3 CSV):
 *  the main phone must parse; an optional cell with a value must parse too
 *  and is sent as E.164; an empty optional cell stays empty. A row that
 *  fails is never sent, and its error names the column for optional cells,
 *  e.g. "mobile_number: Not a valid phone number.". */
export function parseRowNumbers(row: ContactRow, country: CountrySelection): RowNumbersResult {
    const phone = parsePhoneNumber(row.phone, country);
    if (!phone.ok) return { ok: false, error: phone.error };
    const optional = (key: "mobile_number" | "business_number"): { ok: true; e164: string } | { ok: false; error: string } => {
        const value = (row[key] ?? "").trim();
        if (!value) return { ok: true, e164: "" };
        const parsed = parsePhoneNumber(value, country);
        return parsed.ok ? parsed : { ok: false, error: `${key}: ${parsed.error}` };
    };
    const mobile = optional("mobile_number");
    if (!mobile.ok) return mobile;
    const business = optional("business_number");
    if (!business.ok) return business;
    return { ok: true, numbers: { e164: phone.e164, mobile_e164: mobile.e164, business_e164: business.e164 } };
}

export type UploadRow = {
    numbers: RowNumbers;
    row: ContactRow;
};

export type ContactsUpload = {
    csv: string;
    /** Backend row number in the uploaded CSV → row number in the user's original file. */
    rowMap: Map<number, number>;
};

function esc(s: string): string {
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function buildContactsUpload(rows: UploadRow[]): ContactsUpload {
    const rowMap = new Map<number, number>();
    const lines = rows.map(({ numbers, row }, index) => {
        rowMap.set(index + UPLOAD_FIRST_DATA_ROW, row.source_row);
        return [
            numbers.e164,
            row.full_name,
            row.first_name,
            row.last_name,
            row.email,
            row.company,
            numbers.mobile_e164,
            numbers.business_e164,
            row.job_title,
            row.best_time_to_call,
            row.timezone,
            row.calling_notes,
            row.preferred_contact_method,
            row.do_not_call,
        ].map(esc).join(",");
    });
    return { csv: [CONTACT_UPLOAD_COLUMNS.join(",")].concat(lines).join("\n"), rowMap };
}

export function uploadFileFromCsv(csv: string): File {
    return new File([csv], "contacts.csv", { type: "text/csv" });
}
