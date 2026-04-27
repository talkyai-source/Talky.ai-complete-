"use client";

import { QueryCache, QueryClient, QueryClientProvider, MutationCache } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { isApiClientError, setSessionExpiredHandler } from "@/lib/http-client";
import { notificationsStore } from "@/lib/notifications";
import { captureException } from "@/lib/monitoring";

// Auth-flow paths must NOT bounce to login when they themselves get a 401
// (the login endpoint can legitimately reject bad credentials). Add new
// auth screens here if/when they're introduced.
const AUTH_FLOW_PATHS = [
    "/auth/login",
    "/auth/signup",
    "/auth/register",
    "/auth/reset-password",
    "/auth/forgot-password",
];

function isOnAuthFlowPath() {
    if (typeof window === "undefined") return false;
    const p = window.location.pathname;
    return AUTH_FLOW_PATHS.some((auth) => p === auth || p.startsWith(auth + "/"));
}

function isHealthQuery(query: { queryKey?: unknown } | undefined) {
    const key = query?.queryKey;
    return Array.isArray(key) && key[0] === "health";
}

function createAppQueryClient(onUnauthorized: () => void) {
    const onError = (error: Error, query?: { queryKey?: unknown }) => {
        if (isHealthQuery(query)) return;
        if (isApiClientError(error)) {
            if (error.code === "unauthorized") {
                // The redirect itself is now driven by the http-client's
                // session-expired handler (see setSessionExpiredHandler
                // wired below). Here we just surface the toast.  Calling
                // onUnauthorized again is a defensive no-op — the latch
                // in the http-client makes it idempotent.
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
import { AuthProvider } from "@/lib/auth-context";
import dynamic from "next/dynamic";

// Keep notification toaster lazy + client-only (Next 15 SSR safety).
const NotificationToaster = dynamic(
    () => import("@/components/notifications/notification-toaster").then(m => m.NotificationToaster),
    { ssr: false }
);

export function AppProviders({ children }: { children: React.ReactNode }) {
    const router = useRouter();

    // Single source of truth for redirecting on token expiry. Used by:
    //   1. The http-client's setSessionExpiredHandler hook — fires for
    //      any caller that gets a 401, including pages that bypass
    //      react-query and use try/catch directly (dashboard, ai-options,
    //      campaign detail, etc).
    //   2. React-Query's onError — keeps showing the "Session expired"
    //      toast.  The redirect itself is driven by the http-client now,
    //      but this stays as a defensive belt-and-braces call.
    const redirectToLogin = () => {
        if (isOnAuthFlowPath()) {
            // Already on login — don't loop.  The login form's own
            // 401 handling (bad credentials) shows its inline error.
            return;
        }
        // Toast for the case where the http-client fired the handler
        // before any react-query error reached us.
        try {
            notificationsStore.create({
                type: "error",
                title: "Session expired",
                message: "Please log in again.",
            });
        } catch {
            // notifications store can be unavailable during very early
            // boot — never block the redirect on a UI side-effect.
        }
        try {
            router.push("/auth/login");
        } catch {
            window.location.href = "/auth/login";
        }
    };

    const [client] = useState(() => createAppQueryClient(redirectToLogin));

    // Register the http-client-level handler exactly once per provider
    // mount.  Because the http-client uses a fired-latch the handler
    // only runs on the FIRST 401 of the session — re-registering on
    // re-renders won't multiply notifications.
    useEffect(() => {
        setSessionExpiredHandler(redirectToLogin);
        return () => setSessionExpiredHandler(null);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    return (
        <QueryClientProvider client={client}>
            <ThemeProvider>
                <AuthProvider>
                    {children}
                    <NotificationToaster />
                </AuthProvider>
            </ThemeProvider>
        </QueryClientProvider>
    );
}
