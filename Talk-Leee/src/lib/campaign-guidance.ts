/**
 * Campaign guidance (the operator-written `additional_instructions`, sent as
 * `system_prompt`) has ONE character budget, and the backend refuses to save
 * or start a campaign over it. Mirror of `TELEPHONY_TENANT_PROMPT_MAX_CHARS`
 * (backend default 12000). The preview endpoint returns the live value as
 * `campaign_guidance_budget_chars`; pass it in when you have it.
 *
 * Nothing is trimmed on either side: until 2026-09-02 the live call quietly
 * removed the middle of an over-budget script while this UI showed all of it.
 */
export const CAMPAIGN_GUIDANCE_CHAR_BUDGET = 12000;

/**
 * Guidance is compulsory in the UI (frontend rule only — the backend accepts an
 * empty string for API clients). A campaign with no goal produces an agent
 * with nothing campaign-specific to say; 40 characters is roughly one
 * sentence naming who we call and why.
 */
export const CAMPAIGN_GUIDANCE_MIN_CHARS = 40;

export interface GuidanceBudgetStatus {
    chars: number;
    budget: number;
    remaining: number;
    overBudget: boolean;
    /** Empty, whitespace-only, or under CAMPAIGN_GUIDANCE_MIN_CHARS (trimmed). */
    tooShort: boolean;
    /** Save may proceed. */
    valid: boolean;
    /** Human message when invalid, else null. */
    message: string | null;
}

export function guidanceBudgetStatus(
    text: string,
    budget: number = CAMPAIGN_GUIDANCE_CHAR_BUDGET,
): GuidanceBudgetStatus {
    const chars = text.length;
    const trimmed = text.trim().length;
    const remaining = budget - chars;
    const overBudget = chars > budget;
    const tooShort = trimmed < CAMPAIGN_GUIDANCE_MIN_CHARS;
    let message: string | null = null;
    if (overBudget) {
        message =
            `Campaign guidance is ${chars.toLocaleString("en-US")} characters; the limit is ` +
            `${budget.toLocaleString("en-US")}. Nothing is trimmed automatically — shorten it, ` +
            `or move facts into Company knowledge, which is retrieved per turn and does not ` +
            `count against this limit.`;
    } else if (trimmed === 0) {
        message =
            `Campaign guidance is required — say who the agent is calling and what it should ` +
            `achieve (at least ${CAMPAIGN_GUIDANCE_MIN_CHARS} characters).`;
    } else if (tooShort) {
        message =
            `Campaign guidance needs at least ${CAMPAIGN_GUIDANCE_MIN_CHARS} characters ` +
            `(${trimmed} so far) — one sentence on who you call and why.`;
    }
    return {
        chars,
        budget,
        remaining,
        overBudget,
        tooShort,
        valid: !overBudget && !tooShort,
        message,
    };
}
