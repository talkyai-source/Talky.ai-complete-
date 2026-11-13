"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";

/**
 * QA F4-09 (QA-SEC-042): this page used to sign the visitor in with any
 * access_token placed in the URL, so an attacker's link could sign a victim
 * into the attacker's account (login CSRF). No flow links here with a token
 * any more, so a URL token is never verified or applied: the page cleans the
 * address bar, shows a provider error if one came back, and otherwise asks
 * for a normal sign-in.
 */
function AuthCallbackInner() {
    const router = useRouter();
    const [status] = useState("Processing authentication...");
    const [error, setError] = useState("");

    useEffect(() => {
        const id = window.setTimeout(() => {
            const hash = new URLSearchParams(window.location.hash.substring(1));
            const query = new URLSearchParams(window.location.search);
            const errorCode = hash.get("error") || query.get("error");
            const description = hash.get("error_description") || query.get("error_description");
            const carriedToken = Boolean(hash.get("access_token") || query.get("access_token"));

            // Tokens and provider errors must not remain in browser history.
            window.history.replaceState(window.history.state, "", window.location.pathname);
            if (errorCode) {
                setError(description || `Authentication error: ${errorCode}`);
            } else if (carriedToken) {
                setError("Please sign in with your email and password to continue. This link cannot establish your browser session.");
            } else {
                setError("This link could not confirm your account. Please sign in or request a new verification code.");
            }
        }, 0);
        return () => window.clearTimeout(id);
    }, []);

    return (
        <div className="min-h-screen bg-neutral-50 flex items-center justify-center p-4">
            <div className="w-full max-w-md text-center">
                <div className="bg-white rounded-lg border border-gray-200 p-8 shadow-sm">
                    {error ? (
                        <>
                            <div className="w-16 h-16 mx-auto mb-4 bg-red-100 rounded-full flex items-center justify-center">
                                <span className="text-2xl text-red-500">!</span>
                            </div>
                            <h2 className="text-xl font-semibold text-gray-900 mb-2">Authentication failed</h2>
                            <p className="text-gray-500 mb-6">{error}</p>
                            <button
                                onClick={() => router.push("/auth/login")}
                                className="px-4 py-2 bg-gray-900 text-white rounded-lg hover:bg-gray-800"
                            >
                                Try again
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
