"use client";

import { QueryClientProvider } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { setSessionExpiredHandler } from "@/lib/http-client";
import { createAppQueryClient } from "@/lib/app-query-client";
import { NotificationsIdentityProvider } from "@/lib/notifications-client";

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


import { ThemeProvider } from "./theme-provider";
import { AuthProvider } from "@/lib/auth-context";
import { PrefetchOnAuth } from "./prefetch-on-auth";
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
        // This callback has no originating request identity. Query/mutation
        // error notifications are emitted through their captured origin instead.
        // Preserve where the user was so the post-login redirect can
        // bring them back. /dashboard is the default destination for a
        // direct login, so don't bother passing next=/dashboard — let
        // the login page use its canonical default.
        let target = "/auth/login";
        try {
            if (typeof window !== "undefined") {
                const here = `${window.location.pathname}${window.location.search}`;
                if (here && here !== "/" && here !== "/dashboard") {
                    target = `/auth/login?next=${encodeURIComponent(here)}`;
                }
            }
        } catch {
            // window access can fail in odd render conditions — fall back
            // to the bare login URL.
        }
        try {
            router.push(target);
        } catch {
            // Last-resort hard navigation if the router isn't usable yet.
            // Resolved against the origin so it is unambiguously absolute.
            window.location.assign(new URL(target, window.location.origin).toString());
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
                    <NotificationsIdentityProvider />
                    <PrefetchOnAuth />
                    {children}
                    <NotificationToaster />
                </AuthProvider>
            </ThemeProvider>
        </QueryClientProvider>
    );
}
