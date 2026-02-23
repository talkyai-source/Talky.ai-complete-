"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";

/**
 * Auth callback page.
 * Previously handled Supabase magic-link / OTP token exchanges.
 * Now simply redirects to /auth/login since we use password-based auth.
 */
export default function AuthCallbackPage() {
    const router = useRouter();

    useEffect(() => {
        // Redirect to login after a brief moment
        const t = window.setTimeout(() => {
            router.replace("/auth/login");
        }, 500);
        return () => window.clearTimeout(t);
    }, [router]);

    return (
        <div className="min-h-screen bg-neutral-50 flex items-center justify-center p-4">
            <div className="w-full max-w-md text-center">
                <div className="bg-white rounded-lg border border-gray-200 p-8 shadow-sm">
                    <Loader2 className="w-12 h-12 mx-auto mb-4 animate-spin text-gray-400" />
                    <h2 className="text-xl font-semibold text-gray-900 mb-2">Redirecting</h2>
                    <p className="text-gray-500">Taking you to sign in...</p>
                </div>
            </div>
        </div>
    );
}
