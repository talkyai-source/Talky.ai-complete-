"use client";

/**
 * Warms the React Query cache once the user is authenticated, so the main
 * pages already have their data before the user navigates to them — instead
 * of each page fetching from scratch on mount.
 *
 * Mounted once at the app root (inside AuthProvider + QueryClientProvider).
 * Best-effort: prefetch failures are swallowed and never affect the UI.
 */
import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { aiOptionsKeys, prefetchAiOptions, useAiOptionsScope } from "@/lib/queries/ai-options-queries";

export function PrefetchOnAuth() {
    const scope = useAiOptionsScope();
    const queryClient = useQueryClient();

    useEffect(() => {
        if (!scope) return;
        const lifetime = new AbortController();
        // Fire-and-forget; each prefetch is independently best-effort.
        void prefetchAiOptions(queryClient, scope, lifetime.signal);
        return () => {
            lifetime.abort();
            void queryClient.cancelQueries({ queryKey: aiOptionsKeys.scoped(scope) });
            queryClient.removeQueries({ queryKey: aiOptionsKeys.scoped(scope) });
        };
    }, [scope, queryClient]);

    return null;
}

export default PrefetchOnAuth;
