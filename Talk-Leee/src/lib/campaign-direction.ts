/**
 * Inbound campaigns share the `campaigns` table with outbound ones (the
 * inbound config row points at a base campaign whose `direction` is
 * "inbound"), and `GET /campaigns` returns both. Every outbound surface —
 * the Campaigns table, contact upload, TTS apply, email/meeting lead
 * pickers — must filter with this, or it offers inbound campaigns controls
 * the backend rejects with 409 `inbound_campaign_managed_separately`.
 *
 * A missing direction means a historical row: the column defaults to
 * "outbound" and is NOT NULL, so the client mirrors that default.
 */
export function isOutboundCampaign(campaign: { direction?: "inbound" | "outbound" | string | null }): boolean {
    return campaign.direction === "outbound" || campaign.direction === undefined;
}

export function outboundCampaignsOnly<T extends { direction?: "inbound" | "outbound" | string | null }>(campaigns: readonly T[]): T[] {
    return campaigns.filter(isOutboundCampaign);
}

export function inboundCampaignHrefForBase(
    baseCampaignId: string,
    campaigns: readonly { id: string; campaign_id: string }[],
): string {
    const match = campaigns.find((campaign) => campaign.campaign_id === baseCampaignId);
    return match
        ? `/inbound-campaigns/${encodeURIComponent(match.id)}`
        : "/inbound-campaigns";
}
