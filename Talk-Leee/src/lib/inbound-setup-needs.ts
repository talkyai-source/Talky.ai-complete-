/**
 * What an inbound campaign still needs before its number setup can be saved,
 * in plain words. One wording for the two places that say it: next to the
 * switched-off button on step 2, and above step 1 before any work is done.
 */
export function inboundSetupNeeds({ needsCampaign, numbers, trunks }: {
    /** True when the form lets the user pick the AI campaign and none can be picked. */
    needsCampaign: boolean;
    /** Verified phone numbers that can be assigned. */
    numbers: number;
    /** Inbound trunks that are connected right now. */
    trunks: number;
}): string[] {
    return [
        needsCampaign ? "an inbound AI campaign to attach" : null,
        numbers === 0 ? "a verified phone number (none is available: ask an administrator to verify one)" : null,
        trunks === 0 ? "an inbound trunk that is connected right now" : null,
    ].filter((item): item is string => item !== null);
}
