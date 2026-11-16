"use client";

import { useEffect } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { useAuth } from "@/hooks/useAuth";

/**
 * P1: the page used to confirm that a browser exception reaches Sentry. Once
 * the visitor is signed in it throws one uncaught error, outside React, so the
 * browser reports it the way it reports any real one. It shows nothing of its
 * own, is linked from nowhere and is not in the sitemap.
 */
export default function SentryExamplePage() {
    const { user } = useAuth();
    const signedIn = Boolean(user);

    useEffect(() => {
        if (!signedIn) return;
        const timer = window.setTimeout(() => {
            throw new Error("Sentry Example Frontend Error");
        }, 1000);
        return () => window.clearTimeout(timer);
    }, [signedIn]);

    return <DashboardLayout>{null}</DashboardLayout>;
}
