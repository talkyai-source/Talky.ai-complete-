export type LeadInterestState = "interested" | "not_interested" | "unknown";

function verdictToken(value: string | null | undefined): string {
    return (value ?? "")
        .split("|", 1)[0]
        .trim()
        .toLowerCase()
        .replace(/[\s-]+/g, "_");
}

/**
 * Translate the post-call lead verdict into the one claim the UI needs to
 * make: did the analysis actually classify this person as worth following up?
 *
 * Capturing a phone number or company name is deliberately not enough. A
 * caller can provide details and still say no, so deriving the green badge
 * from the presence of captured fields would turn a rejection into a lead.
 */
export function leadInterestState(value: string | null | undefined): LeadInterestState {
    const token = verdictToken(value);

    if (
        token === "qualified"
        || token === "interested"
        || token === "callback"
        || token === "goal_achieved"
        || token === "positive"
    ) {
        return "interested";
    }

    if (
        token === "no_interest"
        || token === "not_interested"
        || token === "disqualified"
        || token === "unqualified"
        || token === "goal_not_achieved"
        || token === "negative"
        || token === "unsuccessful"
    ) {
        return "not_interested";
    }

    return "unknown";
}
