/**
 * Where "Create campaign" sends the user afterwards.
 *
 * An inbound number needs a base AI campaign, and the server will only bind a
 * campaign that is already inbound or an UNUSED outbound draft (a campaign that
 * has dialled out keeps its history and cannot change direction). Until now the
 * inbound form could only offer campaigns that already existed, so a tenant
 * whose campaigns had all run saw every option greyed out and no way forward.
 *
 * The inbound form now links to the campaign creator with `?for=inbound`; the
 * creator honours that by returning to the inbound form with the new draft
 * pre-selected instead of opening the outbound campaign page.
 */
export const INBOUND_RETURN = "inbound";

export const NEW_CAMPAIGN_FOR_INBOUND_HREF = `/campaigns/new?for=${INBOUND_RETURN}`;

export function afterCampaignCreateHref(campaignId: string, returnTo: string | null | undefined): string {
    if (returnTo === INBOUND_RETURN) {
        return `/inbound-campaigns/new?campaign_id=${encodeURIComponent(campaignId)}`;
    }
    return `/campaigns/${encodeURIComponent(campaignId)}`;
}
