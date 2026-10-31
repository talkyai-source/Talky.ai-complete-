"use client";

import { useCallback, useMemo, useState } from "react";
import { keepPreviousData, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { supportApi, type Ticket, type TicketListItem, type TicketStatus } from "@/lib/support-api";
import { supportQueryKeys } from "@/lib/support-config";

/*
 * Ticket reads for the /support pages. The list endpoint filters one status
 * per request, while the "Open" tab covers both `open` and `in_progress`, so a
 * tab is a set of statuses: one query per status, merged newest first, each
 * with its own cursor. This is a workaround for the backend's single-status
 * filter and is recorded in docs/support/backend-gaps.md.
 */

export type TicketTab = "open" | "waiting" | "resolved" | "closed";

export const TICKET_TABS: Array<{ id: TicketTab; label: string; statuses: TicketStatus[] }> = [
    { id: "open", label: "Open", statuses: ["open", "in_progress"] },
    { id: "waiting", label: "Waiting on you", statuses: ["waiting_on_customer"] },
    { id: "resolved", label: "Resolved", statuses: ["resolved"] },
    { id: "closed", label: "Closed", statuses: ["closed"] },
];

export function mergeNewestFirst(pages: TicketListItem[][]): TicketListItem[] {
    const seen = new Set<string>();
    return pages.flat()
        .filter((item) => (seen.has(item.id) ? false : (seen.add(item.id), true)))
        .sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at) || b.id.localeCompare(a.id));
}

export function useTicketTab(tab: TicketTab, scope: "mine" | "team", enabled: boolean) {
    const statuses = useMemo(() => TICKET_TABS.find((entry) => entry.id === tab)?.statuses ?? [], [tab]);
    // Cursors already loaded per status, newest page first; [] means the first page only.
    const [cursors, setCursors] = useState<Record<string, string[]>>({});
    const key = `${tab}:${scope}`;
    const pageList = useMemo(() => statuses.flatMap((status) => [null, ...(cursors[`${key}:${status}`] ?? [])].map((cursor) => ({ status, cursor }))), [statuses, cursors, key]);
    const results = useQueries({
        queries: pageList.map(({ status, cursor }) => ({
            queryKey: supportQueryKeys.tickets(scope, status, cursor),
            queryFn: ({ signal }: { signal: AbortSignal }) => supportApi.listTickets({ status, scope, cursor }, signal),
            enabled,
            placeholderData: keepPreviousData,
            staleTime: 15_000,
        })),
    });
    const items = useMemo(() => mergeNewestFirst(results.map((result) => result.data?.items ?? [])), [results]);
    const loading = enabled && results.some((result) => result.isPending);
    const error = results.find((result) => result.error)?.error ?? null;
    const nextCursors = statuses.map((status) => {
        const known = cursors[`${key}:${status}`] ?? [];
        const last = results[pageList.findIndex((page) => page.status === status && page.cursor === (known.at(-1) ?? null))];
        return { status, cursor: last?.data?.next_cursor ?? null };
    }).filter((entry) => entry.cursor !== null) as Array<{ status: TicketStatus; cursor: string }>;
    const loadMore = useCallback(() => {
        setCursors((current) => {
            const next = { ...current };
            for (const entry of nextCursors) {
                const list = next[`${key}:${entry.status}`] ?? [];
                if (!list.includes(entry.cursor)) next[`${key}:${entry.status}`] = [...list, entry.cursor];
            }
            return next;
        });
    }, [nextCursors, key]);
    const refetch = useCallback(() => { for (const result of results) void result.refetch(); }, [results]);
    return { items, loading, error, hasMore: nextCursors.length > 0, loadMore, refetch };
}

export function useLatestTickets(enabled: boolean, limit = 5) {
    const query = useQuery({
        queryKey: supportQueryKeys.tickets("mine", null, null),
        queryFn: ({ signal }) => supportApi.listTickets({ scope: "mine" }, signal),
        enabled,
        staleTime: 15_000,
    });
    return { ...query, items: (query.data?.items ?? []).slice(0, limit) };
}

export function useTicket(ticketId: string | null, enabled: boolean) {
    return useQuery({
        queryKey: supportQueryKeys.ticket(ticketId ?? "missing"),
        queryFn: ({ signal }) => supportApi.getTicket(ticketId!, signal),
        enabled: enabled && !!ticketId,
        staleTime: 10_000,
        retry: false,
    });
}

/** After a write, put the returned ticket in the cache and refresh the lists and the unread count. */
export function useCommitTicket() {
    const queryClient = useQueryClient();
    return useCallback((ticket: Ticket) => {
        queryClient.setQueryData(supportQueryKeys.ticket(ticket.id), ticket);
        void queryClient.invalidateQueries({ queryKey: supportQueryKeys.ticketsAll });
        void queryClient.invalidateQueries({ queryKey: ["support", "unread"] });
    }, [queryClient]);
}
