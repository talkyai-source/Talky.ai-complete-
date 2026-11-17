export type CampaignNextAction =
    | "schedule_callback"
    | "send_email"
    | "submit_form"
    | "transfer"
    | "end_call";

export interface CampaignBriefLeadField {
    field_key: string;
    label: string;
}

export interface CampaignEmailAction { subject: string; body: string }
export type CampaignFormField = "email" | "phone" | "follow_up" | "project_type" | "bidding_active";
export interface CampaignFormAction {
    name: string;
    recipient: string;
    subject: string;
    fields: CampaignFormField[];
}

export interface CampaignTransferDestination { name: string; number: string; when: string }
export type CampaignTransferDay = "mon" | "tue" | "wed" | "thu" | "fri" | "sat" | "sun";
export interface CampaignTransferHours {
    timezone: string;
    days: CampaignTransferDay[];
    start: string;
    end: string;
}

export const CAMPAIGN_TRANSFER_DAYS: ReadonlyArray<{ value: CampaignTransferDay; short: string; long: string }> = [
    { value: "mon", short: "Mon", long: "Monday" },
    { value: "tue", short: "Tue", long: "Tuesday" },
    { value: "wed", short: "Wed", long: "Wednesday" },
    { value: "thu", short: "Thu", long: "Thursday" },
    { value: "fri", short: "Fri", long: "Friday" },
    { value: "sat", short: "Sat", long: "Saturday" },
    { value: "sun", short: "Sun", long: "Sunday" },
];
export const MAX_TRANSFER_DESTINATIONS = 5;
export const MAX_TRANSFER_NAME_CHARS = 40;
export const MAX_TRANSFER_WHEN_CHARS = 200;

export type CampaignTransferIfNoAnswer = "return_to_agent" | "end_call";

export interface CampaignBriefDraft {
    decision_maker_role: string;
    approved_next_actions: CampaignNextAction[];
    transfer_destination: string;
    /** Rows of the "Who to transfer to" editor; undefined until the user edits (or the saved brief has a list). */
    transfer_destinations?: CampaignTransferDestination[];
    /** undefined = "Only during these hours" is off. */
    transfer_hours?: CampaignTransferHours;
    /** Raw text of the "Ring for" box; undefined or "" = not set (the standard 30 seconds). */
    transfer_ring_seconds?: string;
    /** undefined = not set (the standard behaviour: stay with the caller). */
    transfer_if_no_answer?: CampaignTransferIfNoAnswer;
    opening_objective: string;
    max_objection_attempts: number;
    email_action?: CampaignEmailAction;
    form_action?: CampaignFormAction;
}

export interface CampaignBrief {
    representative_name: string;
    brand: string;
    decision_maker_role: string | null;
    approved_next_actions: CampaignNextAction[];
    transfer_destination: string | null;
    transfer_destinations?: CampaignTransferDestination[] | null;
    transfer_hours?: CampaignTransferHours | null;
    transfer_ring_seconds?: number | null;
    transfer_if_no_answer?: CampaignTransferIfNoAnswer | null;
    required_lead_fields: CampaignBriefLeadField[];
    opening_objective: string | null;
    max_objection_attempts: number;
    email_action?: CampaignEmailAction | null;
    form_action?: CampaignFormAction | null;
}

export const EMPTY_CAMPAIGN_BRIEF_DRAFT: CampaignBriefDraft = {
    decision_maker_role: "",
    approved_next_actions: [],
    transfer_destination: "",
    opening_objective: "",
    max_objection_attempts: 2,
};

export const CAMPAIGN_NEXT_ACTION_OPTIONS: ReadonlyArray<{
    value: CampaignNextAction;
    label: string;
    description: string;
}> = [
    {
        value: "schedule_callback",
        label: "Schedule callback",
        description: "Offer a specific follow-up time after the caller agrees.",
    },
    {
        value: "send_email",
        label: "Send follow-up email",
        description: "Send only after the caller confirms the address and content.",
    },
    {
        value: "submit_form",
        label: "Submit configured form",
        description: "Submit only through an available form tool with a success result.",
    },
    {
        value: "transfer",
        label: "Transfer to a human",
        description: "Hand the live call to a person on your list, only when a transfer is possible.",
    },
    {
        value: "end_call",
        label: "End call politely",
        description: "Close after the objective is complete or the caller declines.",
    },
];

const ACTION_LABELS: Record<CampaignNextAction, string> = {
    schedule_callback: "schedule a callback",
    send_email: "send a follow-up email",
    submit_form: "submit the configured form",
    transfer: "transfer to the approved destination",
    end_call: "end the call politely",
};

function normalizeStructuredText(value: string): string {
    const withoutControls = Array.from(value, (character) => {
        const code = character.charCodeAt(0);
        return code <= 8 || code === 11 || code === 12 || (code >= 14 && code <= 31) || code === 127
            ? ""
            : character;
    }).join("");
    return withoutControls
        .replaceAll("{", "(")
        .replaceAll("}", ")")
        .replace(/\s+/g, " ")
        .trim();
}

export function campaignBriefDraft(value?: Partial<CampaignBrief> | null): CampaignBriefDraft {
    return {
        decision_maker_role: value?.decision_maker_role?.trim() ?? "",
        approved_next_actions: [...(value?.approved_next_actions ?? [])],
        transfer_destination: value?.transfer_destination?.trim() ?? "",
        opening_objective: value?.opening_objective?.trim() ?? "",
        max_objection_attempts: value?.max_objection_attempts ?? 2,
        ...(value?.transfer_destinations?.length
            ? { transfer_destinations: value.transfer_destinations.map((item) => ({ name: item.name, number: item.number, when: item.when ?? "" })) }
            : {}),
        ...(value?.transfer_hours ? { transfer_hours: { ...value.transfer_hours, days: [...value.transfer_hours.days] } } : {}),
        ...(value?.transfer_ring_seconds != null ? { transfer_ring_seconds: String(value.transfer_ring_seconds) } : {}),
        ...(value?.transfer_if_no_answer ? { transfer_if_no_answer: value.transfer_if_no_answer } : {}),
        ...(value?.email_action ? { email_action: { ...value.email_action } } : {}),
        ...(value?.form_action ? { form_action: { ...value.form_action, fields: [...value.form_action.fields] } } : {}),
    };
}

export function buildCampaignBrief({
    draft,
    brand,
    representativeNames,
    requiredLeadFields,
}: {
    draft: CampaignBriefDraft;
    brand: string;
    representativeNames: string[];
    requiredLeadFields: CampaignBriefLeadField[];
}): CampaignBrief {
    return {
        representative_name: normalizeStructuredText(representativeNames[0] ?? ""),
        brand: normalizeStructuredText(brand),
        decision_maker_role: normalizeStructuredText(draft.decision_maker_role),
        approved_next_actions: [...new Set(draft.approved_next_actions)],
        transfer_destination: transferListInUse(draft) ? null : normalizeStructuredText(draft.transfer_destination),
        ...(transferListInUse(draft) ? { transfer_destinations: cleanTransferRows(draft.transfer_destinations ?? []) } : {}),
        ...(draft.approved_next_actions.includes("transfer") && draft.transfer_hours
            ? { transfer_hours: cleanTransferHours(draft.transfer_hours) }
            : {}),
        ...(draft.approved_next_actions.includes("transfer") && draft.transfer_ring_seconds?.trim()
            ? { transfer_ring_seconds: Number(draft.transfer_ring_seconds.trim()) }
            : {}),
        ...(draft.approved_next_actions.includes("transfer") && draft.transfer_if_no_answer
            ? { transfer_if_no_answer: draft.transfer_if_no_answer }
            : {}),
        required_lead_fields: requiredLeadFields.map((field) => ({
            field_key: normalizeStructuredText(field.field_key),
            label: normalizeStructuredText(field.label),
        })),
        opening_objective: normalizeStructuredText(draft.opening_objective),
        max_objection_attempts: draft.max_objection_attempts,
        ...(draft.email_action ? { email_action: { subject: draft.email_action.subject.trim(), body: draft.email_action.body.trim() } } : {}),
        ...(draft.form_action ? { form_action: { ...draft.form_action, name: draft.form_action.name.trim(), subject: draft.form_action.subject.trim(), recipient: draft.form_action.recipient.trim(), fields: [...draft.form_action.fields] } } : {}),
    };
}

/** The rows the editor shows: the saved list, or the legacy single value as one unnamed row. */
export function transferRowsForEditor(draft: CampaignBriefDraft): CampaignTransferDestination[] {
    return draft.transfer_destinations?.length
        ? draft.transfer_destinations
        : [{ name: "", number: draft.transfer_destination, when: "" }];
}

/** True once the user (or a saved list) has made the named list the source; false keeps the legacy single value. */
function transferListInUse(draft: CampaignBriefDraft): boolean {
    if (!draft.approved_next_actions.includes("transfer")) return false;
    const rows = draft.transfer_destinations ?? [];
    return rows.length > 1 || rows.some((row) => row.name.trim() || row.number.trim() || row.when.trim());
}

function cleanTransferRows(rows: CampaignTransferDestination[]): CampaignTransferDestination[] {
    return rows.map((row) => ({
        name: normalizeStructuredText(row.name),
        number: row.number.trim(),
        when: normalizeStructuredText(row.when),
    }));
}

function cleanTransferHours(hours: CampaignTransferHours): CampaignTransferHours {
    return {
        timezone: hours.timezone,
        days: CAMPAIGN_TRANSFER_DAYS.map((day) => day.value).filter((day) => hours.days.includes(day)),
        start: hours.start,
        end: hours.end,
    };
}

// Mirrors backend transfer_validation (E.164, extension, feature code, named extension,
// phone-presentation text, SIP URI). The backend stays the authority and its message is shown if it refuses.
const TRANSFER_NUMBER_PATTERNS = [
    /^\+[1-9][0-9]{6,14}$/,
    /^[0-9]{1,32}$/,
    /^[*#][0-9*#]{0,31}$/,
    /^[A-Za-z][A-Za-z0-9_.-]{0,63}$/,
    /^sips?:[A-Za-z0-9_+.-]+@[A-Za-z0-9][A-Za-z0-9.-]*(:[0-9]{1,5})?(;[A-Za-z0-9_.=:+-]+)*$/i,
];

function looksLikeTransferNumber(value: string): boolean {
    if (TRANSFER_NUMBER_PATTERNS.some((pattern) => pattern.test(value))) return true;
    if (!/^[+0-9(). -]{7,40}$/.test(value)) return false;
    const digits = value.replace(/\D/g, "");
    return digits.length >= 7 && digits.length <= 15;
}

export interface CampaignTransferRowErrors { name?: string; number?: string; when?: string }
export interface CampaignTransferErrors {
    rows: CampaignTransferRowErrors[];
    days?: string;
    start?: string;
    end?: string;
    ring?: string;
}

export const TRANSFER_ERROR_TEXT = {
    nameRequired: "Enter a name for this destination.",
    nameTooLong: `Name must be ${MAX_TRANSFER_NAME_CHARS} characters or fewer.`,
    nameDuplicate: "Give each destination a different name (capital letters do not make a name different).",
    numberInvalid: "Enter a valid phone number, for example +442071234567.",
    whenRequired: "Describe when to transfer here (needed when there are two or more destinations).",
    whenTooLong: `Keep this to ${MAX_TRANSFER_WHEN_CHARS} characters or fewer.`,
    daysRequired: "Choose at least one day.",
    startRequired: "Choose a start time.",
    endRequired: "Choose an end time.",
    ringInvalid: "Enter a whole number from 10 to 60.",
    endNotAfterStart: "End time must be later than the start time. Overnight hours are not supported.",
} as const;

/** Per-field transfer problems, matching the backend rules. Empty when nothing is wrong or the list is not in use. */
export function campaignTransferErrors(draft: CampaignBriefDraft): CampaignTransferErrors {
    const result: CampaignTransferErrors = { rows: [] };
    if (transferListInUse(draft)) {
        const rows = cleanTransferRows(draft.transfer_destinations ?? []);
        const seen = new Set<string>();
        result.rows = rows.map((row) => {
            const errors: CampaignTransferRowErrors = {};
            const key = row.name.toLocaleLowerCase();
            if (!row.name) errors.name = TRANSFER_ERROR_TEXT.nameRequired;
            else if (row.name.length > MAX_TRANSFER_NAME_CHARS) errors.name = TRANSFER_ERROR_TEXT.nameTooLong;
            else if (seen.has(key)) errors.name = TRANSFER_ERROR_TEXT.nameDuplicate;
            if (row.name) seen.add(key);
            if (!looksLikeTransferNumber(row.number)) errors.number = TRANSFER_ERROR_TEXT.numberInvalid;
            if (row.when.length > MAX_TRANSFER_WHEN_CHARS) errors.when = TRANSFER_ERROR_TEXT.whenTooLong;
            else if (rows.length > 1 && !row.when) errors.when = TRANSFER_ERROR_TEXT.whenRequired;
            return errors;
        });
    }
    const hours = draft.approved_next_actions.includes("transfer") ? draft.transfer_hours : undefined;
    if (hours) {
        if (!hours.days.length) result.days = TRANSFER_ERROR_TEXT.daysRequired;
        if (!hours.start) result.start = TRANSFER_ERROR_TEXT.startRequired;
        if (!hours.end) result.end = TRANSFER_ERROR_TEXT.endRequired;
        if (hours.start && hours.end && hours.start >= hours.end) result.end = TRANSFER_ERROR_TEXT.endNotAfterStart;
    }
    const ring = draft.approved_next_actions.includes("transfer") ? draft.transfer_ring_seconds?.trim() : "";
    if (ring && !(/^\d+$/.test(ring) && Number(ring) >= 10 && Number(ring) <= 60)) {
        result.ring = TRANSFER_ERROR_TEXT.ringInvalid;
    }
    return result;
}

function hasTransferErrors(errors: CampaignTransferErrors): boolean {
    return Boolean(errors.days || errors.start || errors.end || errors.ring
        || errors.rows.some((row) => row.name || row.number || row.when));
}

export function campaignBriefValidation(draft: CampaignBriefDraft): string | null {
    if (!draft.opening_objective.trim()) {
        return "Add an opening objective so the representative knows what a successful first exchange should achieve.";
    }
    if (draft.opening_objective.length > 500) {
        return "Opening objective must be 500 characters or fewer.";
    }
    if (draft.decision_maker_role.length > 160) {
        return "Decision-maker role must be 160 characters or fewer.";
    }
    if (!Number.isInteger(draft.max_objection_attempts)
        || draft.max_objection_attempts < 1
        || draft.max_objection_attempts > 5) {
        return "Maximum objection attempts must be from 1 to 5.";
    }
    const transferApproved = draft.approved_next_actions.includes("transfer");
    if (transferApproved && !transferListInUse(draft) && !draft.transfer_destination.trim()) {
        return "Add a transfer destination or remove Transfer from the approved next actions.";
    }
    if (!transferApproved && (draft.transfer_destination.trim() || draft.transfer_destinations?.length || draft.transfer_hours
        || draft.transfer_ring_seconds?.trim() || draft.transfer_if_no_answer)) {
        return "Select Transfer as an approved next action or clear the transfer destination.";
    }
    if (hasTransferErrors(campaignTransferErrors(draft))) {
        return "Fix the transfer settings marked above before saving.";
    }
    if (draft.email_action && (!draft.email_action.subject.trim() || !draft.email_action.body.trim())) {
        return "Add the approved email subject and message, or remove its configuration.";
    }
    if (draft.form_action && (!draft.form_action.name.trim() || !draft.form_action.subject.trim()
        || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(draft.form_action.recipient.trim()) || !draft.form_action.fields.length)) {
        return "Add the form name, destination email, subject and at least one field.";
    }
    return null;
}

export type CampaignBriefInvalidField =
    | "opening_objective" | "decision_maker_role" | "max_objection_attempts" | "transfer_action" | "transfer_destination"
    | "email_subject" | "email_body" | "form_name" | "form_recipient" | "form_subject" | "form_fields";

/**
 * The fields the message of campaignBriefValidation is about: the same checks
 * in the same order. Empty when the brief is valid, and for the transfer
 * summary, whose own errors are already shown beside their fields.
 */
export function campaignBriefInvalidFields(draft: CampaignBriefDraft): CampaignBriefInvalidField[] {
    if (!draft.opening_objective.trim() || draft.opening_objective.length > 500) return ["opening_objective"];
    if (draft.decision_maker_role.length > 160) return ["decision_maker_role"];
    if (!Number.isInteger(draft.max_objection_attempts)
        || draft.max_objection_attempts < 1
        || draft.max_objection_attempts > 5) {
        return ["max_objection_attempts"];
    }
    const transferApproved = draft.approved_next_actions.includes("transfer");
    if (transferApproved && !transferListInUse(draft) && !draft.transfer_destination.trim()) return ["transfer_destination"];
    if (!transferApproved && (draft.transfer_destination.trim() || draft.transfer_destinations?.length || draft.transfer_hours
        || draft.transfer_ring_seconds?.trim() || draft.transfer_if_no_answer)) {
        return ["transfer_action"];
    }
    if (hasTransferErrors(campaignTransferErrors(draft))) return [];
    if (draft.email_action && (!draft.email_action.subject.trim() || !draft.email_action.body.trim())) {
        return [
            ...(!draft.email_action.subject.trim() ? ["email_subject" as const] : []),
            ...(!draft.email_action.body.trim() ? ["email_body" as const] : []),
        ];
    }
    if (draft.form_action) {
        const fields: CampaignBriefInvalidField[] = [
            ...(!draft.form_action.name.trim() ? ["form_name" as const] : []),
            ...(!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(draft.form_action.recipient.trim()) ? ["form_recipient" as const] : []),
            ...(!draft.form_action.subject.trim() ? ["form_subject" as const] : []),
            ...(!draft.form_action.fields.length ? ["form_fields" as const] : []),
        ];
        if (fields.length) return fields;
    }
    return [];
}

export function renderCampaignBrief(brief: CampaignBrief): string {
    const lines = ["## CAMPAIGN BRIEF"];
    if (brief.representative_name) {
        lines.push(`- Representative on this call: ${brief.representative_name}`);
    }
    if (brief.brand) lines.push(`- Brand represented: ${brief.brand}`);
    if (brief.decision_maker_role) {
        lines.push(`- Intended decision-maker role: ${brief.decision_maker_role}`);
    }
    if (brief.opening_objective) {
        lines.push(`- Opening objective: ${brief.opening_objective}`);
    }
    const actions = brief.approved_next_actions.map((action) => ACTION_LABELS[action]);
    if (actions.length) lines.push(`- Approved next actions: ${actions.join("; ")}`);
    if (brief.transfer_destinations?.length) {
        lines.push(`- Approved transfer destinations: ${brief.transfer_destinations
            .map((item) => (item.when ? `${item.name} (${item.when})` : item.name))
            .join("; ")}`);
    } else if (brief.transfer_destination) {
        lines.push(`- Approved transfer destination: ${brief.transfer_destination}`);
    }
    if (brief.required_lead_fields.length) {
        lines.push(
            `- Required lead fields: ${brief.required_lead_fields
                .map((field) => `${field.label} (${field.field_key})`)
                .join("; ")}`,
        );
    }
    lines.push(`- Maximum objection-handling attempts: ${brief.max_objection_attempts}`);
    lines.push(
        "Only take an approved next action after its runtime tool reports success. "
        + "A configured destination does not mean transfer is currently available.",
    );
    return lines.join("\n");
}

export function campaignBriefGuidanceText(
    additionalInstructions: string,
    brief: CampaignBrief,
): string {
    return [renderCampaignBrief(brief), additionalInstructions.trim()].filter(Boolean).join("\n\n");
}
