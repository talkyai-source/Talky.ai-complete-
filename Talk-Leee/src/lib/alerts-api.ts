"use client";

/**
 * Hooks for the Alert Timeline panel on /campaigns.
 *
 * Replaces the old client-side `seedAlerts()` with real GET / POST
 * against `/api/v1/alerts*`. Polls every 10s for the list (paused on
 * hidden tab). Ack and Resolve are mutations that invalidate the list
 * query on success so the UI reflects server state immediately.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { backendApi } from "@/lib/backend-api";
import type { AlertItem, AlertSeverity, AlertStatus, AlertType } from "@/lib/campaign-performance";

type RawAlertItem = {
    id: string;
    title: string;
    description: string | null;
    severity: string;
    type: string;
    status: string;
    createdAt: string;
    updatedAt: string;
    acknowledged: boolean;
    relatedCampaignIds: string[];
    metadata: Record<string, unknown> | null;
    resolutionNotes: string | null;
};

const VALID_SEVERITIES: AlertSeverity[] = ["Critical", "Warning", "Info"];
const VALID_TYPES: AlertType[] = ["Network", "API", "Campaign", "System"];
const VALID_STATUSES: AlertStatus[] = ["Active", "Investigating", "Resolved"];

function coerceMetadata(raw: Record<string, unknown> | null): Record<string, string | number | boolean> | undefined {
    if (!raw) return undefined;
    const out: Record<string, string | number | boolean> = {};
    for (const [k, v] of Object.entries(raw)) {
        if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") {
            out[k] = v;
        }
    }
    return Object.keys(out).length > 0 ? out : undefined;
}

function mapAlert(raw: RawAlertItem): AlertItem {
    return {
        id: raw.id,
        title: raw.title,
        description: raw.description ?? "",
        severity: (VALID_SEVERITIES.includes(raw.severity as AlertSeverity)
            ? raw.severity
            : "Info") as AlertSeverity,
        type: (VALID_TYPES.includes(raw.type as AlertType)
            ? raw.type
            : "System") as AlertType,
        status: (VALID_STATUSES.includes(raw.status as AlertStatus)
            ? raw.status
            : "Active") as AlertStatus,
        createdAt: raw.createdAt,
        updatedAt: raw.updatedAt,
        acknowledged: raw.acknowledged,
        relatedCampaignIds: raw.relatedCampaignIds ?? [],
        metadata: coerceMetadata(raw.metadata),
    };
}

export const alertsQueryKeys = {
    all: ["alerts"] as const,
    list: (filters: {
        severity: AlertSeverity[];
        type: AlertType[];
        status: AlertStatus[];
    }) => ["alerts", "list", filters] as const,
};

export function useAlerts(filters: {
    severity: Set<AlertSeverity>;
    type: Set<AlertType>;
    status: Set<AlertStatus>;
}) {
    const filterArrays = {
        severity: Array.from(filters.severity).sort(),
        type: Array.from(filters.type).sort(),
        status: Array.from(filters.status).sort(),
    };
    return useQuery({
        queryKey: alertsQueryKeys.list(filterArrays),
        queryFn: async ({ signal }): Promise<AlertItem[]> => {
            const data = await backendApi.alerts.list(
                {
                    severity: filterArrays.severity,
                    status: filterArrays.status,
                    alert_type: filterArrays.type,
                    limit: 200,
                },
                signal,
            );
            return data.items.map((r) => mapAlert(r as RawAlertItem));
        },
        refetchInterval: () => {
            if (typeof document === "undefined") return false;
            return document.visibilityState === "hidden" ? false : 10_000;
        },
        refetchOnWindowFocus: true,
        refetchOnReconnect: true,
        staleTime: 5_000,
        retry: 1,
    });
}

export function useAckAlert() {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: async ({ alertId, note }: { alertId: string; note?: string }) => {
            const data = await backendApi.alerts.ack(alertId, note);
            return mapAlert(data as RawAlertItem);
        },
        onSuccess: () => {
            void qc.invalidateQueries({ queryKey: alertsQueryKeys.all });
        },
    });
}

export function useResolveAlert() {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: async ({ alertId, resolution_notes }: { alertId: string; resolution_notes: string }) => {
            const data = await backendApi.alerts.resolve(alertId, resolution_notes);
            return mapAlert(data as RawAlertItem);
        },
        onSuccess: () => {
            void qc.invalidateQueries({ queryKey: alertsQueryKeys.all });
        },
    });
}
