"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { flushSync } from "react-dom";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { isBearerFallbackEnabled } from "@/lib/auth-token";
import { markFreshLogin } from "@/lib/http-client";
import { captureException } from "@/lib/monitoring";
import { postAuthDashboard } from "@/lib/post-auth-navigation";
import { Loader2 } from "lucide-react";

function AuthCallbackInner() {
    const router = useRouter();
    const { applyLoginResult, accessToken } = useAuth();
    const identityRef = useRef(accessToken);
    useEffect(() => { identityRef.current = accessToken; }, [accessToken]);
    const [status, setStatus] = useState("Processing authentication...");
    const [error, setError] = useState("");

    useEffect(() => {
        let cancelled = false;
        const previousIdentity = identityRef.current;
        const id = window.setTimeout(async () => {
            try {
                const hash = new URLSearchParams(window.location.hash.substring(1));
                const query = new URLSearchParams(window.location.search);
                const token = hash.get("access_token") || query.get("access_token");
                const errorCode = hash.get("error") || query.get("error");
                const description = hash.get("error_description") || query.get("error_description");

                // Tokens and provider errors must not remain in browser history.
                window.history.replaceState(window.history.state, "", window.location.pathname);
                if (errorCode) {
                    setError(description || `Authentication error: ${errorCode}`);
                    return;
                }
                if (!token) {
                    setError("This link could not confirm your account. Please sign in or request a new verification code.");
                    return;
                }
                if (!isBearerFallbackEnabled()) {
                    // A URL token cannot establish the HttpOnly cookie session
                    // required in this mode. A reload would lose it or restore
                    // an older cookie identity, so require the normal login.
                    setError("Please sign in with your email and password to continue. This link cannot establish your browser session.");
                    return;
                }

                setStatus("Verifying your account...");
                // Check this exact token at the canonical authority before
                // replacing an existing login. Never create a profile or fall
                // back to the previous account's refresh cookie here.
                const me = await api.getMe(token);
                if (cancelled) return;
                if (identityRef.current !== previousIdentity) {
                    setError("Your sign-in changed while this link was being checked. Please sign in again.");
                    return;
                }
                flushSync(() => {
                    applyLoginResult({
                        user_id: me.id,
                        tenant_id: me.tenant_id,
                        email: me.email,
                        role: me.role,
                        business_name: me.business_name,
                        minutes_remaining: me.minutes_remaining,
                        minutes_state: me.minutes_state,
                        access_token: token,
                    });
                });
                markFreshLogin();
                window.location.assign(postAuthDashboard(me.role));
            } catch (err) {
                if (cancelled) return;
                captureException(err, { area: "auth-callback" });
                setError(err instanceof Error ? err.message : "Authentication failed. Please sign in again.");
            }
        }, 0);
        return () => {
            cancelled = true;
            window.clearTimeout(id);
        };
    }, [applyLoginResult]);

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
