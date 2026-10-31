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
import { useNotificationsState } from "@/lib/notifications-client";
import { notificationScopeKey, notificationsStore } from "@/lib/notifications";

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
    list: (filter: EventQuickFilter, scopeKey: string | null, generation: number) =>
        ["events", scopeKey, generation, "list", filter] as const,
};

export function useEventStream(filter: EventQuickFilter) {
    const { scopeKey, generation, hydrated } = useNotificationsState();
    return useQuery({
        queryKey: eventsQueryKeys.list(filter, scopeKey, generation),
        enabled: Boolean(scopeKey) && hydrated,
        queryFn: async ({ signal }): Promise<StreamEvent[]> => {
            const origin = notificationsStore.capture();
            if (!origin.isCurrent() || origin.scopeKey !== scopeKey || origin.generation !== generation) {
                throw new DOMException("Account changed", "AbortError");
            }
            const categories = FILTER_TO_BACKEND[filter];
            const data = await backendApi.events.list(
                { categories, limit: 100 },
                AbortSignal.any([signal, origin.signal]),
            );
            if (!origin.isCurrent()) throw new DOMException("Account changed", "AbortError");
            // A cookie can change in another tab before its lifecycle marker
            // reaches this one. Bind server facts to the authenticated recipient,
            // never just to the browser identity that initiated the request.
            if (typeof data?.tenant_id !== "string" || typeof data?.user_id !== "string"
                || notificationScopeKey({ tenantId: data.tenant_id, userId: data.user_id }) !== origin.scopeKey) {
                throw new DOMException("Event response owner could not be verified", "AbortError");
            }
            return data.items.map(mapEvent);
        },
        refetchInterval: () => {
            if (typeof document === "undefined") return false;
            return document.visibilityState === "hidden" ? false : 10_000;
        },
        refetchOnWindowFocus: true,
        refetchOnReconnect: true,
        staleTime: 5_000,
        // Identity rejection is final for this observation; wait for the actual
        // account lifecycle change rather than retrying under unverified cookies.
        retry: (failureCount, error) => error.name !== "AbortError" && failureCount < 1,
    });
}
