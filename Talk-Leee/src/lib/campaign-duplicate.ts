/**
 * Campaign Duplicate (production-readiness 3.1): the ONE copy routine shared
 * by the campaign list and the campaign detail page.
 *
 * The copy is made by the backend, in one transaction: `POST
 * /campaigns/{id}/duplicate` (backend api/v1/endpoints/campaigns.py,
 * services/campaign_duplication.py). It creates a draft named "<name> - Copy"
 * with the source's settings (prompt, voice, pacing, caller line and calling
 * schedule, concurrent calls, retries), its knowledge files and its lead
 * fields. Contacts, calls, results and counters are never copied. A refusal
 * (not found, inbound, knowledge not ready, ...) arrives as the error's message.
 */
import { dashboardApi, type Campaign } from "@/lib/dashboard-api";

export function duplicateSuccessText(newName: string): string {
    return `Duplicated as “${newName}”. The copy is a draft with the same settings, knowledge files and lead fields. Contacts, calls and results are not copied.`;
}

export type CampaignDuplicateResult = {
    /** The created campaign, exactly as the duplicate route returned it. */
    campaign: Campaign;
    message: string;
};

/**
 * Create a real backend copy of `sourceId`.
 *
 * Rejects (and creates nothing) when the backend refuses: the error carries the
 * server's message.
 */
export async function duplicateCampaign(sourceId: string): Promise<CampaignDuplicateResult> {
    const { campaign } = await dashboardApi.duplicateCampaign(sourceId);
    return { campaign, message: duplicateSuccessText(campaign.name) };
}

export type CampaignDuplicateNotice = { kind: "success" | "error"; text: string };

// The detail page opens the copy's page after a duplicate; the result message
// has to be showing there. Held in memory only, for one page change.
const pendingNotices = new Map<string, CampaignDuplicateNotice>();

export function stashDuplicateNotice(campaignId: string, notice: CampaignDuplicateNotice): void {
    pendingNotices.set(campaignId, notice);
}

export function peekDuplicateNotice(campaignId: string): CampaignDuplicateNotice | null {
    return pendingNotices.get(campaignId) ?? null;
}

export function clearDuplicateNotice(campaignId: string): void {
    pendingNotices.delete(campaignId);
}
