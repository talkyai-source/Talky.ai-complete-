"use client";

import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@/lib/auth-context";
import { supportApi } from "@/lib/support-api";
import { supportQueryKeys, useSupportConfig } from "@/lib/support-config";

/*
 * GET /support/unread every 60 seconds and on window focus, only while support
 * is enabled for the signed-in account. The diff below is what the notifier
 * uses to say "New reply on SUP-…" once per newly unread ticket.
 */

export const UNREAD_REFETCH_MS = 60_000;

export type UnreadSnapshot = { count: number; ticketIds: string[] };

/** The ticket ids that became unread since the previous snapshot, only when the count rose. */
export function newlyUnread(previous: UnreadSnapshot | null, next: UnreadSnapshot): string[] {
    if (!previous) return [];
    if (next.count <= previous.count) return [];
    const seen = new Set(previous.ticketIds);
    return next.ticketIds.filter((ticketId) => !seen.has(ticketId));
}

export function useSupportUnread() {
    const { user } = useAuth();
    const { enabled, canReport } = useSupportConfig();
    const active = enabled && canReport;
    const query = useQuery({
        queryKey: supportQueryKeys.unread(user?.id ?? null),
        queryFn: async ({ signal }): Promise<UnreadSnapshot> => {
            const result = await supportApi.unread(signal);
            return { count: result.count, ticketIds: result.ticket_ids };
        },
        enabled: active,
        refetchInterval: active ? UNREAD_REFETCH_MS : false,
        refetchOnWindowFocus: active,
        staleTime: 15_000,
        retry: false,
    });
    return { snapshot: active ? (query.data ?? null) : null, count: active ? (query.data?.count ?? 0) : 0 };
}
