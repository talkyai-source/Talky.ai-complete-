/**
 * Shape checks for the two answers the Contacts page renders from. An answer
 * that is not what the page expects is reported as an error (the app's
 * existing "Invalid response format", see lib/api.ts parseOrThrow) instead of
 * being rendered and crashing the page.
 */
import { ApiClientError } from "@/lib/http-client";
import type { Campaign, Contact } from "@/lib/dashboard-api";

function invalid(url: string): ApiClientError {
    return new ApiClientError({ code: "invalid_response", message: "Invalid response format", url, method: "GET" });
}

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null;

export function checkCampaignList(data: unknown): { campaigns: Campaign[] } {
    if (!isRecord(data) || !Array.isArray(data.campaigns)
        || !data.campaigns.every((c) => isRecord(c) && typeof c.id === "string" && typeof c.name === "string")) {
        throw invalid("/campaigns");
    }
    return data as { campaigns: Campaign[] };
}

export function checkContactsPage<T extends { items: Contact[]; total: number }>(data: T, campaignId: string): T {
    const value: unknown = data;
    if (!isRecord(value) || typeof value.total !== "number" || !Array.isArray(value.items)
        || !value.items.every((c) => isRecord(c) && typeof c.id === "string" && typeof c.phone_number === "string")) {
        throw invalid(`/campaigns/${campaignId}/contacts`);
    }
    return data;
}
