"use client";

import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@/lib/auth-context";
import { supportApi, type SupportConfig } from "@/lib/support-api";

/*
 * The shipping gate. After sign-in, GET /support/config decides whether any
 * support UI exists. The query resolves to null (never throws) on an error,
 * a 404 or enabled=false, so a backend without support, or with it switched
 * off, produces no Help button, no sidebar entry and no unread polling, and
 * no error toast from the query cache.
 */

export const supportQueryKeys = {
    config: (userId: string | null) => ["support", "config", userId ?? "anonymous"] as const,
    unread: (userId: string | null) => ["support", "unread", userId ?? "anonymous"] as const,
    tickets: (scope: string, status: string | null, cursor: string | null) => ["support", "tickets", scope, status ?? "all", cursor ?? "first"] as const,
    ticketsAll: ["support", "tickets"] as const,
    ticket: (ticketId: string) => ["support", "ticket", ticketId] as const,
    articles: (q: string) => ["support", "articles", q] as const,
};

export async function loadSupportConfig(signal?: AbortSignal): Promise<SupportConfig | null> {
    try {
        const config = await supportApi.config(signal);
        return config.enabled ? config : null;
    } catch {
        return null;
    }
}

export type SupportGate = {
    /** The config when support is on for this account, null when it is off or unknown. */
    config: SupportConfig | null;
    /** True until the first answer arrives for a signed-in user. */
    loading: boolean;
    enabled: boolean;
    canReport: boolean;
    canReadTeam: boolean;
};

export function useSupportConfig(): SupportGate {
    const { user, status } = useAuth();
    const signedIn = status === "authenticated" && !!user;
    const query = useQuery({
        queryKey: supportQueryKeys.config(user?.id ?? null),
        queryFn: ({ signal }) => loadSupportConfig(signal),
        enabled: signedIn,
        staleTime: 5 * 60_000,
        retry: false,
        refetchOnWindowFocus: false,
    });
    const config = signedIn && query.data ? query.data : null;
    return {
        config,
        loading: signedIn && query.isPending,
        enabled: config !== null,
        canReport: config?.permissions.report === true,
        canReadTeam: config?.permissions.read_team === true,
    };
}
