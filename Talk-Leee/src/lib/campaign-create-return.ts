/**
 * Where the campaign creator sends the user afterwards (2026-09-09).
 *
 * An inbound campaign is created with the SAME wizard/form as an outbound one
 * (identical options), born inbound. When that creator runs as step 1 of
 * `/inbound-campaigns/new`, it continues to step 2 — the verified number,
 * trunk, opening, hours and safety routing — with the new campaign locked in.
 */
export const INBOUND_RETURN = "inbound";

export function afterCampaignCreateHref(campaignId: string, returnTo: string | null | undefined): string {
    if (returnTo === INBOUND_RETURN) {
        return `/inbound-campaigns/new?campaign_id=${encodeURIComponent(campaignId)}`;
    }
    return `/campaigns/${encodeURIComponent(campaignId)}`;
}
