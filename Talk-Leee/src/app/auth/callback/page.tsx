"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { apiBaseUrl } from "@/lib/env";
import { captureException } from "@/lib/monitoring";
import { Loader2 } from "lucide-react";

function AuthCallbackInner() {
    const router = useRouter();
    const searchParams = useSearchParams();
    const [status, setStatus] = useState("Processing authentication...");
    const [error, setError] = useState("");

    const handleCallback = useCallback(async () => {
        try {
            // Supabase magic link tokens come in URL hash (#) or query params
            // Check for access_token in hash fragment first
            let accessToken: string | null = null;
            let refreshToken: string | null = null;

            if (typeof window !== "undefined") {
                // Parse hash fragment
                const hashParams = new URLSearchParams(window.location.hash.substring(1));
                accessToken = hashParams.get("access_token");
                refreshToken = hashParams.get("refresh_token");

                // Also check query params as fallback
                if (!accessToken) {
                    const urlParams = new URLSearchParams(window.location.search);
                    accessToken = urlParams.get("access_token");
                    refreshToken = urlParams.get("refresh_token");
                }

                // Check for error in params
                const errorCode = hashParams.get("error") || searchParams.get("error");
                const errorDescription = hashParams.get("error_description") || searchParams.get("error_description");

                if (errorCode) {
                    setError(errorDescription || `Authentication error: ${errorCode}`);
                    return;
                }
            }

            if (accessToken) {
                setStatus("Authenticated! Redirecting...");

                // Store the token
                api.setToken(accessToken);

                // Also store refresh token if available
                if (refreshToken && typeof window !== "undefined") {
                    localStorage.setItem("refresh_token", refreshToken);
                }

                // Try to create profile if this is first login (registration)
                try {
                    await fetch(
                        `${apiBaseUrl()}/auth/create-profile`,
                        {
                            method: "POST",
                            headers: {
                                "Authorization": `Bearer ${accessToken}`,
                                "Content-Type": "application/json"
                            }
                        }
                    );
                    // Ignore errors - profile might already exist
                } catch {
                    // Ignore - profile creation is optional
                }

                const rawNext = searchParams.get("next");
                const safeNext =
                    rawNext && rawNext.startsWith("/") && !rawNext.startsWith("//") ? rawNext : null;

                let role: string | null = null;
                try {
                    const me = await api.getMe();
                    role = me.role;
                } catch {
                    role = null;
                }

                router.push(role === "white_label_admin" ? "/white-label/dashboard" : safeNext ?? "/dashboard");
            } else {
                // No token found - might be a different callback type
                // Check if this is a Supabase email confirmation
                const type = searchParams.get("type");

                if (type === "signup" || type === "recovery" || type === "invite") {
                    setStatus("Email confirmed! Redirecting to login...");
                    setTimeout(() => router.push("/auth/login"), 2000);
                } else {
                    setError("No authentication token found. Please try logging in again.");
                }
            }
        } catch (err) {
            captureException(err, { area: "auth-callback" });
            setError(err instanceof Error ? err.message : "Authentication failed");
        }
    }, [router, searchParams]);

    useEffect(() => {
        const id = window.setTimeout(() => {
            void handleCallback();
        }, 0);

        return () => window.clearTimeout(id);
    }, [handleCallback]);

    return (
        <div className="min-h-screen bg-neutral-50 flex items-center justify-center p-4">
            <div className="w-full max-w-md text-center">
                <div className="bg-white rounded-lg border border-gray-200 p-8 shadow-sm">
                    {error ? (
                        <>
                            <div className="w-16 h-16 mx-auto mb-4 bg-red-100 rounded-full flex items-center justify-center">
                                <span className="text-2xl text-red-500">!</span>
                            </div>
                            <h2 className="text-xl font-semibold text-gray-900 mb-2">Authentication Failed</h2>
                            <p className="text-gray-500 mb-6">{error}</p>
                            <button
                                onClick={() => router.push("/auth/login")}
                                className="px-4 py-2 bg-gray-900 text-white rounded-lg hover:bg-gray-800"
                            >
                                Try Again
                            </button>
                        </>
                    ) : (
                        <>
                            <Loader2 className="w-12 h-12 mx-auto mb-4 animate-spin text-gray-400" />
                            <h2 className="text-xl font-semibold text-gray-900 mb-2">Authenticating</h2>
                            <p className="text-gray-500">{status}</p>
                        </>
                    )}
                </div>
            </div>
        </div>
    );
}

export default function AuthCallbackPage() {
    return (
        <Suspense
            fallback={
                <div className="min-h-screen bg-neutral-50 flex items-center justify-center p-4">
                    <div className="w-full max-w-md text-center">
                        <div className="bg-white rounded-lg border border-gray-200 p-8 shadow-sm">
                            <Loader2 className="w-12 h-12 mx-auto mb-4 animate-spin text-gray-400" />
                            <h2 className="text-xl font-semibold text-gray-900 mb-2">Authenticating</h2>
                            <p className="text-gray-500">Processing authentication...</p>
                        </div>
                    </div>
                </div>
            }
        >
            <AuthCallbackInner />
        </Suspense>
    );
}
