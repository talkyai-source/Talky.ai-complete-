/**
 * Campaign Duplicate (production-readiness 3.1): the ONE copy routine shared
 * by the campaign list and the campaign detail page.
 *
 * There is no backend duplicate endpoint, so a copy is a real
 * `POST /campaigns` built from the source's saved row, followed by a copy of
 * its lead fields. Contacts, calls and counters are never sent: the copy
 * shows whatever the backend reports for the new row.
 *
 * Knowledge can only be uploaded, not linked or copied, so a campaign that
 * has any knowledge source is refused here as well as in the UI.
 */
import { api } from "@/lib/api";
import { callingSchedulePayload } from "@/lib/calling-schedule-text";
import { isOutboundCampaign } from "@/lib/campaign-direction";
import { dashboardApi, type Campaign, type CampaignCreate, type PersonaType } from "@/lib/dashboard-api";
import { ApiClientError } from "@/lib/http-client";
import { leadDetailsApi } from "@/lib/lead-details-api";

export const DUPLICATE_KNOWLEDGE_BLOCKED_TEXT = "Duplicate isn't available yet for campaigns with knowledge files.";

export function duplicateSuccessText(newName: string): string {
    return `Duplicated as “${newName}”. These settings use their defaults on the copy: pacing, caller line, concurrent calls and retries.`;
}

export function duplicateLeadFieldsFailedText(newName: string): string {
    return `Duplicated as “${newName}”, but its lead fields could not be copied. Set them on the copy's edit page.`;
}

export function duplicateCampaignName(sourceName: string): string {
    return `${sourceName} - Copy`;
}

/** Thrown instead of copying when the source has knowledge (or it cannot be read). */
export class CampaignDuplicateBlockedError extends Error {
    constructor() {
        super(DUPLICATE_KNOWLEDGE_BLOCKED_TEXT);
        this.name = "CampaignDuplicateBlockedError";
    }
}

export type CampaignDuplicateResult = {
    /** The created campaign, exactly as `POST /campaigns` returned it. */
    campaign: Campaign;
    leadFieldsCopied: boolean;
    message: string;
};

/**
 * The knowledge read's answer when the knowledge feature is switched off on
 * the server (CAMPAIGN_KNOWLEDGE_ENABLED): a 404 whose detail is this fixed
 * text (backend campaign_knowledge.py:70). The same route's other 404 is
 * "Campaign not found" with code `campaign_not_found`, so the text is what
 * tells them apart; there is no dedicated code for "feature off".
 */
export const KNOWLEDGE_FEATURE_OFF_DETAIL = "Campaign knowledge is not enabled";

function knowledgeFeatureOff(err: unknown): boolean {
    return err instanceof ApiClientError && err.status === 404 && err.message === KNOWLEDGE_FEATURE_OFF_DETAIL;
}

/**
 * True when Duplicate must stay disabled: the campaign has at least one
 * knowledge source, or its knowledge could not be read. With the knowledge
 * feature off there is no knowledge a copy could miss, so that one answer
 * counts as "no knowledge"; every other failed read still blocks.
 */
export async function campaignDuplicateBlocked(campaignId: string): Promise<boolean> {
    try {
        const knowledge = await api.getCampaignKnowledge(campaignId);
        return !Array.isArray(knowledge?.sources) || knowledge.sources.length > 0;
    } catch (err) {
        return !knowledgeFeatureOff(err);
    }
}

/**
 * The create body for a copy. Field-for-field the same mapping the edit page
 * uses to prefill its form (app/campaigns/[id]/edit/page.tsx), so a copy
 * carries what an unchanged "edit and save" of the source would send.
 */
export function duplicateCampaignPayload(source: Campaign): CampaignCreate {
    const scriptConfig = source.script_config ?? {};
    const genders = scriptConfig.agent_name_genders ?? {};
    const outbound = isOutboundCampaign(source);
    return {
        pipeline_mode: scriptConfig.pipeline_mode,
        realtime_model: scriptConfig.realtime_model,
        realtime_voice: scriptConfig.realtime_voice,
        realtime_settings: scriptConfig.realtime_settings,
        realtime_prompt: scriptConfig.realtime_prompt,
        name: duplicateCampaignName(source.name ?? ""),
        description: source.description || undefined,
        system_prompt: scriptConfig.additional_instructions ?? source.system_prompt ?? "",
        voice_id: source.voice_id ?? "",
        tts_provider: source.tts_provider || undefined,
        goal: (source as { goal?: string | null }).goal || undefined,
        persona_type: (scriptConfig.persona_type ?? "lead_gen") as PersonaType,
        company_name: scriptConfig.company_name ?? "",
        agent_names: scriptConfig.agent_names ?? [],
        agent_name_genders: Object.keys(genders).length > 0 ? genders : undefined,
        campaign_slots: scriptConfig.campaign_slots ?? {},
        campaign_brief: scriptConfig.campaign_brief ?? undefined,
        knowledge_driven: Boolean((scriptConfig as Record<string, unknown>).knowledge_driven),
        direction: outbound ? "outbound" : "inbound",
        ...(outbound ? { calling_schedule: callingSchedulePayload(source.calling_config) } : {}),
    };
}

/**
 * Create a real backend copy of `sourceId`.
 *
 * Rejects (and creates nothing) when the source has knowledge, cannot be
 * read, or the create is refused: the error carries the server's message.
 * Once the copy exists a lead-fields failure does not reject; it is reported
 * in `message` so the caller can still show and open the copy.
 */
export async function duplicateCampaign(sourceId: string): Promise<CampaignDuplicateResult> {
    if (await campaignDuplicateBlocked(sourceId)) throw new CampaignDuplicateBlockedError();
    const { campaign: source } = await dashboardApi.getCampaign(sourceId);
    const payload = duplicateCampaignPayload(source);
    const { campaign } = await dashboardApi.createCampaign(payload);
    const newName = campaign.name || payload.name;
    try {
        const { fields } = await leadDetailsApi.campaignFields(sourceId);
        await leadDetailsApi.setCampaignFields(campaign.id, fields);
    } catch {
        return { campaign, leadFieldsCopied: false, message: duplicateLeadFieldsFailedText(newName) };
    }
    return { campaign, leadFieldsCopied: true, message: duplicateSuccessText(newName) };
}

export type CampaignDuplicateNotice = { kind: "success" | "warning" | "error"; text: string };

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
