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

export interface GuidanceBudgetStatus {
    chars: number;
    budget: number;
    remaining: number;
    overBudget: boolean;
    /** Human message when over budget, else null. */
    message: string | null;
}

export function guidanceBudgetStatus(
    text: string,
    budget: number = CAMPAIGN_GUIDANCE_CHAR_BUDGET,
): GuidanceBudgetStatus {
    const chars = text.length;
    const remaining = budget - chars;
    const overBudget = chars > budget;
    return {
        chars,
        budget,
        remaining,
        overBudget,
        message: overBudget
            ? `Campaign guidance is ${chars.toLocaleString("en-US")} characters; the limit is ` +
              `${budget.toLocaleString("en-US")}. Nothing is trimmed automatically — shorten it, ` +
              `or move facts into Company knowledge, which is retrieved per turn and does not ` +
              `count against this limit.`
            : null,
    };
}
