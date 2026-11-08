"use client";

/**
 * One account-scoped poll for the signed-in alert observers (failed calls,
 * low minutes). Same shape as the lead alerts' event poll
 * (event-stream-api.ts:76-111): every 10 seconds, paused while the tab is
 * hidden, aborted when the verified identity changes. Both endpoints are
 * permission gated (calls.py:31, billing_topups.py:180), so a "forbidden"
 * answer stops the poll instead of repeating its toast every interval.
 */
import { useQuery } from "@tanstack/react-query";
import { isApiClientError } from "@/lib/http-client";
import { notificationsStore } from "@/lib/notifications";

export const ALERT_POLL_MS = 10_000;

function forbidden(error: unknown): boolean {
    return isApiClientError(error) && error.code === "forbidden";
}

export function useAccountAlertQuery<T>(
    name: string,
    scopeKey: string,
    generation: number,
    fetcher: (signal: AbortSignal) => Promise<T>,
) {
    return useQuery({
        queryKey: [name, scopeKey, generation] as const,
        queryFn: async ({ signal }): Promise<T> => {
            const origin = notificationsStore.capture();
            if (!origin.isCurrent() || origin.scopeKey !== scopeKey || origin.generation !== generation) {
                throw new DOMException("Account changed", "AbortError");
            }
            const data = await fetcher(AbortSignal.any([signal, origin.signal]));
            if (!origin.isCurrent()) throw new DOMException("Account changed", "AbortError");
            return data;
        },
        refetchInterval: (query) => {
            if (forbidden(query.state.error)) return false;
            if (typeof document === "undefined") return false;
            return document.visibilityState === "hidden" ? false : ALERT_POLL_MS;
        },
        refetchOnWindowFocus: (query) => !forbidden(query.state.error),
        refetchOnReconnect: true,
        staleTime: 5_000,
        retry: (failureCount, error) => error.name !== "AbortError" && failureCount < 1,
    });
}
