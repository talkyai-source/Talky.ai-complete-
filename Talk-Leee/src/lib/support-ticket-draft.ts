import type { Escalation, SupportConfig } from "@/lib/support-api";

/*
 * The ticket form's data and its rules, mirroring the server's CreateTicket
 * body: subject 3–150 characters on one line, description 10–5000, one of the
 * configured categories, optional related campaign or call.
 */

export const SUBJECT_MIN = 3;
export const SUBJECT_MAX = 150;
export const DESCRIPTION_MIN = 10;
export const DESCRIPTION_MAX = 5000;

export type TicketDraft = {
    subject: string;
    category: string;
    description: string;
    relatedCampaignId: string | null;
    relatedCallId: string | null;
};

export type TicketDraftErrors = Partial<Record<"subject" | "category" | "description", string>>;

export function draftFromEscalation(escalation: Escalation, config: SupportConfig): TicketDraft {
    const known = config.categories.some((category) => category.id === escalation.draft.category);
    return {
        subject: escalation.draft.subject,
        category: known ? escalation.draft.category : (config.categories[0]?.id ?? "other"),
        description: escalation.draft.description,
        relatedCampaignId: escalation.draft.related_campaign_id ?? null,
        relatedCallId: escalation.draft.related_call_id ?? null,
    };
}

function hasControlCharacters(value: string): boolean {
    for (const char of value) {
        const code = char.charCodeAt(0);
        if ((code < 32 && char !== "\n" && char !== "\t") || code === 127) return true;
    }
    return false;
}

export function validateTicketDraft(draft: TicketDraft, config: SupportConfig): TicketDraftErrors {
    const errors: TicketDraftErrors = {};
    const subject = draft.subject.trim();
    if (subject.length < SUBJECT_MIN) errors.subject = `The subject needs at least ${SUBJECT_MIN} characters.`;
    else if (subject.length > SUBJECT_MAX) errors.subject = `The subject can have at most ${SUBJECT_MAX} characters.`;
    else if (/[\n\t]/.test(subject)) errors.subject = "The subject must be one line.";
    else if (hasControlCharacters(subject)) errors.subject = "The subject contains characters that cannot be sent.";
    if (!config.categories.some((category) => category.id === draft.category)) errors.category = "Choose a category.";
    const description = draft.description.replace(/\r\n?/g, "\n").trim();
    if (description.length < DESCRIPTION_MIN) errors.description = `Describe the problem in at least ${DESCRIPTION_MIN} characters.`;
    else if (description.length > DESCRIPTION_MAX) errors.description = `The description can have at most ${DESCRIPTION_MAX} characters.`;
    else if (hasControlCharacters(description)) errors.description = "The description contains characters that cannot be sent.";
    return errors;
}

/** The body for POST /support/tickets, trimmed the way the server trims it. */
export function ticketDraftPayload(draft: TicketDraft, conversationId: string | null, attachmentIds: string[]) {
    return {
        ...(conversationId ? { conversation_id: conversationId } : {}),
        subject: draft.subject.trim().replace(/\s+/g, " "),
        category: draft.category,
        description: draft.description.replace(/\r\n?/g, "\n").trim(),
        ...(draft.relatedCampaignId ? { related_campaign_id: draft.relatedCampaignId } : {}),
        ...(draft.relatedCallId ? { related_call_id: draft.relatedCallId } : {}),
        attachment_ids: attachmentIds,
    };
}
