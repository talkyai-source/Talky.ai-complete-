"use client";

import React, { createContext, useContext, useEffect, useMemo, useState, ReactNode, useCallback } from "react";
import { api } from "@/lib/api";
import { resetSessionExpiredLatch } from "@/lib/http-client";
interface MeResponse {
    id: string;
    email: string;
    name?: string;
    business_name?: string;
    role: string;
    minutes_remaining: number;
    // Admin / suspension fields populated by the backend's /me endpoint
    // when the user has elevated permissions or the tenant/partner is in
    // a non-active state. All optional — older sessions without these
    // fields just see undefined and the SuspensionStateProvider treats
    // that as "not suspended".
    partner_id?: string;
    tenant_id?: string;
    partner_status?: string;
    tenant_status?: string;
    suspended_scope?: string;
    suspension_reason?: string;
    suspended_at?: string;
}

interface AuthContextType {
    user: MeResponse | null;
    loading: boolean;
    login: (email: string, password: string) => Promise<void>;
    register: (email: string, password: string, businessName: string, name?: string) => Promise<void>;
    logout: () => Promise<void>;
    setToken: (token: string) => void;
    refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
    const [user, setUser] = useState<MeResponse | null>(null);
    const [loading, setLoading] = useState(true);

    // On mount, check for existing token and fetch user profile
    useEffect(() => {
        api.getMe()
            .then((me) => setUser(me))
            .catch(() => {
                api.clearToken();
                setUser(null);
            })
            .finally(() => setLoading(false));
    }, []);

    const login = useCallback(async (email: string, password: string) => {
        const res = await api.login(email, password);
        api.setToken(res.access_token);
        // Re-arm the http-client's session-expired latch so the NEXT 401
        // (after this fresh session eventually expires) fires the redirect
        // again.  Without this, a logout → login round-trip would leave
        // the latch tripped and the next expiry would silently no-op.
        resetSessionExpiredLatch();
        setUser({
            id: res.user_id,
            email: res.email,
            role: res.role,
            business_name: res.business_name,
            minutes_remaining: res.minutes_remaining ?? 0,
        });
    }, []);

    const register = useCallback(async (
        email: string,
        password: string,
        businessName: string,
        name?: string,
    ) => {
        const res = await api.register(email, password, businessName, "basic", name);
        api.setToken(res.access_token);
        resetSessionExpiredLatch();
        setUser({
            id: res.user_id,
            email: res.email,
            role: res.role,
            business_name: res.business_name,
            minutes_remaining: res.minutes_remaining ?? 0,
        });
    }, []);

    const logout = useCallback(async () => {
        try {
            await api.logout();
        } catch {
            // Keep logout resilient for explicit user sign-out.
            api.clearToken();
        } finally {
            try {
                localStorage.removeItem("refresh_token");
            } catch { /* ignore */ }
            setUser(null);
        }
    }, []);

    const setToken = useCallback((token: string) => {
        api.setToken(token);
        resetSessionExpiredLatch();
        // After setting token, try to load real user
        api.getMe()
            .then((me) => setUser(me))
            .catch(() => {
                api.clearToken();
                setUser(null);
            });
    }, []);

    const refreshUser = useCallback(async () => {
        setLoading(true);
        try {
            const me = await api.getMe();
            setUser(me);
        } catch {
            api.clearToken();
            setUser(null);
        } finally {
            setLoading(false);
        }
    }, []);

    const value = useMemo(
        () => ({ user, loading, login, register, logout, setToken, refreshUser }),
        [loading, user, login, register, logout, setToken, refreshUser],
    );

    return (
        <AuthContext.Provider value={value}>
            {children}
        </AuthContext.Provider>
    );
}

// SSR-safe default. The client-side hydration replaces this with the real
// context value once <AuthProvider> mounts. Returning a no-op shape (rather
// than throwing) keeps Server Components rendering when consumers like
// SuspensionStateProvider are evaluated during the server pass — including
// inside Sentry's RSC wrapper, which can invoke layouts in a way that
// flattens the client-component boundary.
const SSR_FALLBACK_AUTH_CONTEXT: AuthContextType = {
    user: null,
    loading: true,
    login: async () => {
        throw new Error("useAuth used outside AuthProvider on client");
    },
    register: async () => {
        throw new Error("useAuth used outside AuthProvider on client");
    },
    logout: async () => {
        throw new Error("useAuth used outside AuthProvider on client");
    },
    setToken: () => {
        throw new Error("useAuth used outside AuthProvider on client");
    },
    refreshUser: async () => {
        throw new Error("useAuth used outside AuthProvider on client");
    },
};

export function useAuth() {
    const context = useContext(AuthContext);
    if (context !== undefined) return context;
    // On the server we're either pre-rendering for SSR or running through
    // an RSC wrapper. Returning a safe fallback lets the page produce HTML;
    // hydration on the client replaces this with the real provider value.
    if (typeof window === "undefined") {
        return SSR_FALLBACK_AUTH_CONTEXT;
    }
    // Client-side without a provider is a real bug — keep the loud signal.
    throw new Error("useAuth must be used within an AuthProvider");
}
