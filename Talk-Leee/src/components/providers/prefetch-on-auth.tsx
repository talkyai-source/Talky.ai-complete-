"use client";

/**
 * Warms the React Query cache once the user is authenticated, so the main
 * pages already have their data before the user navigates to them — instead
 * of each page fetching from scratch on mount.
 *
 * Mounted once at the app root (inside AuthProvider + QueryClientProvider).
 * Best-effort: prefetch failures are swallowed and never affect the UI.
 */
import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { useAuth } from "@/hooks/useAuth";
import { prefetchAiOptions } from "@/lib/queries/ai-options-queries";

export function PrefetchOnAuth() {
    const { user } = useAuth();
    const queryClient = useQueryClient();
    const done = useRef(false);

    useEffect(() => {
        if (!user || done.current) return;
        done.current = true;
        // Fire-and-forget; each prefetch is independently best-effort.
        void prefetchAiOptions(queryClient);
    }, [user, queryClient]);

    return null;
}

export default PrefetchOnAuth;
