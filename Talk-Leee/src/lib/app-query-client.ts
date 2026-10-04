import { QueryCache, QueryClient, MutationCache } from "@tanstack/react-query";
import { isApiClientError } from "@/lib/http-client";
import { notificationsStore } from "@/lib/notifications";
import { captureException } from "@/lib/monitoring";
import { captureMutationNotificationOrigin, mutationNotificationOrigin } from "@/lib/notification-mutations";

function isHealthQuery(query: { queryKey?: unknown } | undefined) {
    const key = query?.queryKey;
    return Array.isArray(key) && key[0] === "health";
}

export function createAppQueryClient(onUnauthorized: () => void) {
    const onError = (error: Error, origin: ReturnType<typeof notificationsStore.capture> | undefined, query?: { queryKey?: unknown }) => {
        if (isHealthQuery(query) || !origin?.isCurrent()) return;
        if (isApiClientError(error)) {
            if (error.code === "unauthorized") {
                // The redirect itself is now driven by the http-client's
                // session-expired handler (see setSessionExpiredHandler
                // wired below). Here we just surface the toast.  Calling
                // onUnauthorized again is a defensive no-op — the latch
                // in the http-client makes it idempotent.
                origin.create({ type: "error", title: "Session expired", message: "Please log in again." });
                onUnauthorized();
                return;
            }
            if (error.code === "forbidden") {
                origin.create({ type: "error", title: "Permission denied", message: "You don't have access to that." });
                return;
            }
            if (error.code === "rate_limited") {
                const retryAfter = typeof error.retryAfterMs === "number" ? Math.ceil(error.retryAfterMs / 1000) : undefined;
                origin.create({
                    type: "warning",
                    title: "Rate limited",
                    message: retryAfter ? `Retry in ~${retryAfter}s.` : "Please retry shortly.",
                });
                return;
            }
            if (error.code === "server_error") {
                origin.create({ type: "error", title: "Server error", message: "Something went wrong. Try again." });
                captureException(error, { url: error.url, status: error.status, code: error.code });
                return;
            }
            origin.create({ type: "error", title: "Request failed", message: error.message });
            return;
        }
        origin.create({ type: "error", title: "Unexpected error", message: "Something went wrong." });
        captureException(error);
    };

    const queryOrigins = new WeakMap<object, ReturnType<typeof notificationsStore.capture>>();
    const queryCache = new QueryCache({
        onError: (error, query) => onError(error, queryOrigins.get(query), query),
    });
    // Capture each fetch, including refetches of an existing query. Reading the
    // current identity only in onError would attribute a late A error to B.
    queryCache.subscribe((event) => {
        if (event.type === "updated" && event.action.type === "fetch") {
            queryOrigins.set(event.query, notificationsStore.capture());
        }
    });
    return new QueryClient({
        queryCache,
        mutationCache: new MutationCache({
            // Runs before the per-mutation onMutate await/callback. The same
            // execution context also protects notification-producing hooks.
            onMutate: (_variables, _mutation, context) => {
                captureMutationNotificationOrigin(context);
            },
            onError: (error, _variables, _result, _mutation, context) => {
                onError(error, mutationNotificationOrigin(context));
            },
        }),
        defaultOptions: {
            queries: {
                staleTime: 30_000,
                gcTime: 5 * 60_000,
                retry: (failureCount, err) => {
                    if (isApiClientError(err)) {
                        if (err.code === "unauthorized" || err.code === "forbidden" || err.code === "rate_limited") return false;
                    }
                    return failureCount < 2;
                },
            },
            mutations: {
                retry: (failureCount, err) => {
                    if (isApiClientError(err)) {
                        if (err.code === "unauthorized" || err.code === "forbidden" || err.code === "rate_limited") return false;
                    }
                    return failureCount < 1;
                },
            },
        },
    });
}
