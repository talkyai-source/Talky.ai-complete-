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
            const carriedToken = Boolean(hash.get("access_token") || query.get("access_token"));

            // Tokens and provider errors must not remain in browser history.
            window.history.replaceState(window.history.state, "", window.location.pathname);
            if (errorCode) {
                // The provider's own wording comes from the address and can be
                // written by anyone, so the page says its own words instead.
                setError("The sign-in provider reported a problem. Please try again.");
            } else if (carriedToken) {
                setError("Please sign in with your email and password to continue. This link cannot establish your browser session.");
            } else {
                setError("This link could not confirm your account. Please sign in or request a new verification code.");
            }
        }, 0);
        return () => window.clearTimeout(id);
    }, []);

    return (
        <div className="min-h-screen bg-background flex items-center justify-center p-4">
            <div className="w-full max-w-md text-center">
                <div className="bg-card text-card-foreground rounded-lg border border-border p-8 shadow-sm">
                    {error ? (
                        <>
                            <div className="w-16 h-16 mx-auto mb-4 bg-red-100 dark:bg-red-950 rounded-full flex items-center justify-center">
                                <span className="text-2xl text-red-500">!</span>
                            </div>
                            <h1 className="text-xl font-semibold text-foreground mb-2">Authentication failed</h1>
                            <p className="text-muted-foreground mb-6">{error}</p>
                            <button
                                onClick={() => router.push("/auth/login")}
                                className="px-4 py-2 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90"
                            >
                                Try again
                            </button>
                        </>
                    ) : (
                        <>
                            <Loader2 className="w-12 h-12 mx-auto mb-4 animate-spin text-muted-foreground" />
                            <h1 className="text-xl font-semibold text-foreground mb-2">Authenticating</h1>
                            <p className="text-muted-foreground">{status}</p>
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
                <div className="min-h-screen bg-background flex items-center justify-center p-4">
                    <div className="w-full max-w-md text-center">
                        <div className="bg-card text-card-foreground rounded-lg border border-border p-8 shadow-sm">
                            <Loader2 className="w-12 h-12 mx-auto mb-4 animate-spin text-muted-foreground" />
                            <h1 className="text-xl font-semibold text-foreground mb-2">Authenticating</h1>
                            <p className="text-muted-foreground">Processing authentication...</p>
                        </div>
                    </div>
                </div>
            }
        >
            <AuthCallbackInner />
        </Suspense>
    );
}
