"use client";

import { useEffect, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";

function SettingsIntegrationsContent() {
    const router = useRouter();
    const searchParams = useSearchParams();

    useEffect(() => {
        // Preserve query params for success/error messages
        const params = searchParams.toString();
        const redirectUrl = params ? `/integrations?${params}` : "/integrations";
        router.replace(redirectUrl);
    }, [router, searchParams]);

    return (
        <div className="flex items-center justify-center min-h-screen bg-gray-900">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-white" />
        </div>
    );
}

/**
 * Redirect from /settings/integrations to /integrations
 * This handles the OAuth callback redirect from the backend
 */
export default function SettingsIntegrationsRedirect() {
    return (
        <Suspense fallback={
            <div className="flex items-center justify-center min-h-screen bg-gray-900">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-white" />
            </div>
        }>
            <SettingsIntegrationsContent />
        </Suspense>
    );
}

