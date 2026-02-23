"use client";

import { QueryCache, QueryClient, QueryClientProvider, MutationCache } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { isApiClientError } from "@/lib/http-client";
import { notificationsStore } from "@/lib/notifications";
import { captureException } from "@/lib/monitoring";

function isHealthQuery(query: { queryKey?: unknown } | undefined) {
    const key = query?.queryKey;
    return Array.isArray(key) && key[0] === "health";
}

function createAppQueryClient(onUnauthorized: () => void) {
    const onError = (error: Error, query?: { queryKey?: unknown }) => {
        if (isHealthQuery(query)) return;
        if (isApiClientError(error)) {
            if (error.code === "unauthorized") {
                notificationsStore.create({ type: "error", title: "Session expired", message: "Please log in again." });
                onUnauthorized();
                return;
            }
            if (error.code === "forbidden") {
                notificationsStore.create({ type: "error", title: "Permission denied", message: "You don't have access to that." });
                return;
            }
            if (error.code === "rate_limited") {
                const retryAfter = typeof error.retryAfterMs === "number" ? Math.ceil(error.retryAfterMs / 1000) : undefined;
                notificationsStore.create({
                    type: "warning",
                    title: "Rate limited",
                    message: retryAfter ? `Retry in ~${retryAfter}s.` : "Please retry shortly.",
                });
                return;
            }
            if (error.code === "server_error") {
                notificationsStore.create({ type: "error", title: "Server error", message: "Something went wrong. Try again." });
                captureException(error, { url: error.url, status: error.status, code: error.code });
                return;
            }
            notificationsStore.create({ type: "error", title: "Request failed", message: error.message });
            return;
        }
        notificationsStore.create({ type: "error", title: "Unexpected error", message: "Something went wrong." });
        captureException(error);
    };

    return new QueryClient({
        queryCache: new QueryCache({ onError }),
        mutationCache: new MutationCache({ onError: (error) => onError(error) }),
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

import { ThemeProvider } from "./theme-provider";

export function AppProviders({ children }: { children: React.ReactNode }) {
    const router = useRouter();
    const [client] = useState(() =>
        createAppQueryClient(() => {
            try {
                router.push("/auth/login");
            } catch {
                window.location.href = "/auth/login";
            }
        })
    );

    return (
        <QueryClientProvider client={client}>
            <ThemeProvider>
                {children}
            </ThemeProvider>
        </QueryClientProvider>
    );
}
