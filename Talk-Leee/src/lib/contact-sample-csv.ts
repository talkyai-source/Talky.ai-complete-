/**
 * The sample file offered on the CSV import screen, built in the browser.
 *
 * Canonical column names (the ones both the website reader and the server map
 * with no guessing), two invented people, and numbers from Ofcom's reserved
 * drama range 020 7946 0xxx, which the shared phone check accepts.
 */
export const SAMPLE_CSV_FILENAME = "talklee-sample-contacts.csv";

export const SAMPLE_CSV_HEADERS = [
    "first_name", "last_name", "phone_number", "email", "company_name",
    "mobile_number", "business_number", "job_title", "best_time_to_call",
    "timezone", "calling_notes", "preferred_contact_method", "do_not_call",
] as const;

export const SAMPLE_CSV_ROWS: string[][] = [
    ["Alex", "Example", "+442079460123", "alex.example@example.com", "Example Roofing Ltd",
        "+442079460124", "+442079460125", "Office Manager", "Weekdays 9am-11am",
        "Europe/London", "Asked for a call after the tender closes", "phone", "no"],
    ["Sam", "Sample", "+442079460130", "sam.sample@example.com", "Sample Plumbing Ltd",
        "+442079460131", "+442079460132", "Owner", "Weekdays 2pm-4pm",
        "Europe/London", "Prefers a short call", "phone", "no"],
];

export const SAMPLE_CSV_HELP =
    "Only the phone number is needed. First and last name are strongly recommended, because the agent greets people by name. Every other column is optional; delete the ones you do not use.";

function esc(s: string): string {
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function buildSampleCsv(): string {
    return [SAMPLE_CSV_HEADERS as readonly string[], ...SAMPLE_CSV_ROWS]
        .map((r) => r.map(esc).join(","))
        .join("\n") + "\n";
}
