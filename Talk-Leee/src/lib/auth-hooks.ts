"use client";

/**
 * Universal-auth-state hooks (Phase 2).
 *
 * AuthContext is the single source of truth for the access token + the
 * current user. Components and other hooks that need to know "what's the
 * token right now?" or "build me an Authorization header" should reach
 * for these hooks rather than reading localStorage directly.
 *
 * The reactive contract: when AuthContext.accessToken changes (login,
 * refresh-rotation, logout, cross-tab logout via storage event), every
 * subscriber of useAccessToken() / useAuthHeaders() re-renders. WebSocket
 * builders that depended on a one-time getBrowserAuthToken() at mount can
 * useEffect on the value to reconnect on rotation.
 */
import { useMemo } from "react";

import { useAuth } from "@/lib/auth-context";

/**
 * Subscribe to the current access token.
 * Returns null when the user is anonymous (no session) or before the
 * AuthProvider has mounted on the client.
 */
export function useAccessToken(): string | null {
    return useAuth().accessToken;
}

/**
 * Build an Authorization header from the current access token. Useful for
 * non-shared-client fetch callers; prefer useApiClient() instead since it
 * also handles refresh-on-401 and the fresh-login grace window.
 */
export function useAuthHeaders(): Record<string, string> {
    const token = useAccessToken();
    return useMemo<Record<string, string>>(() => {
        const out: Record<string, string> = {};
        if (token) out.Authorization = `Bearer ${token}`;
        return out;
    }, [token]);
}
