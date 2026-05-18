"use client";

/**
 * Hook for the Event Stream panel on /campaigns.
 *
 * Replaces the old client-side `seedEvents()` + `setInterval(generateEvent, 9000)`
 * with a real GET against `/api/v1/events`. Polls every 10s (paused when
 * the tab is hidden) so users see real campaign/system events emitted by
 * the backend (`emit_event` in app/domain/services/event_emitter.py).
 */
import { useQuery } from "@tanstack/react-query";
import { backendApi } from "@/lib/backend-api";
import type { EventQuickFilter, StreamEvent } from "@/lib/campaign-performance";

type RawStreamEvent = {
    id: string;
    category: string;
    title: string;
    description: string | null;
    severity: string | null;
    related_campaign_id: string | null;
    related_call_id: string | null;
    actor_user_id: string | null;
    metadata: Record<string, unknown> | null;
    created_at: string;
};

const FILTER_TO_BACKEND: Record<EventQuickFilter, string[] | undefined> = {
    All: undefined,
    Campaigns: ["campaign", "milestone"],
    System: ["system"],
    Alerts: ["alert"],
    "User Actions": ["user_action"],
};

const UI_CATEGORY: Record<string, StreamEvent["category"]> = {
    campaign: "Campaign",
    milestone: "Milestones",
    system: "System",
    alert: "Alerts",
    user_action: "User Actions",
};

function coerceMetadata(raw: Record<string, unknown> | null): Record<string, string | number | boolean> | undefined {
    if (!raw) return undefined;
    const out: Record<string, string | number | boolean> = {};
    for (const [k, v] of Object.entries(raw)) {
        if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") {
            out[k] = v;
        } else if (v !== null && v !== undefined) {
            out[k] = String(v);
        }
    }
    return Object.keys(out).length > 0 ? out : undefined;
}

function mapEvent(raw: RawStreamEvent): StreamEvent {
    return {
        id: raw.id,
        category: UI_CATEGORY[raw.category] ?? "System",
        title: raw.title,
        description: raw.description ?? "",
        createdAt: raw.created_at,
        relatedCampaignIds: raw.related_campaign_id ? [raw.related_campaign_id] : [],
        metadata: coerceMetadata(raw.metadata),
    };
}

export const eventsQueryKeys = {
    list: (filter: EventQuickFilter) => ["events", "list", filter] as const,
};

export function useEventStream(filter: EventQuickFilter) {
    return useQuery({
        queryKey: eventsQueryKeys.list(filter),
        queryFn: async ({ signal }): Promise<StreamEvent[]> => {
            const categories = FILTER_TO_BACKEND[filter];
            const data = await backendApi.events.list(
                { categories, limit: 100 },
                signal,
            );
            return data.items.map(mapEvent);
        },
        refetchInterval: () => {
            if (typeof document === "undefined") return false;
            return document.visibilityState === "hidden" ? false : 10_000;
        },
        refetchOnWindowFocus: true,
        refetchOnReconnect: true,
        staleTime: 5_000,
        // First failure surfaces a toast via the http-client's session-expired
        // path; don't retry indefinitely against a broken backend.
        retry: 1,
    });
}
