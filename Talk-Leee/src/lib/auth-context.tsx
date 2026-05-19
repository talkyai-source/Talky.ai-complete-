"use client";

import React, { createContext, useContext, useEffect, useMemo, useState, ReactNode, useCallback } from "react";
import { api } from "@/lib/api";
import { resetSessionExpiredLatch, isWithinFreshLoginGrace, setTokenProvider } from "@/lib/http-client";
import { getBrowserAuthToken, setBrowserAuthToken } from "@/lib/auth-token";
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

// Authentication status — derived from accessToken + user + loading flags.
// "uninitialized": SSR or pre-mount (before AuthProvider's bootstrap runs)
// "loading"     : bootstrap /auth/me in flight on the client
// "authenticated": user object present + token present
// "anonymous"   : no token, no user — present a logged-out shell
export type AuthStatus = "uninitialized" | "loading" | "authenticated" | "anonymous";

interface AuthContextType {
    user: MeResponse | null;
    loading: boolean;
    // Phase 2: reactive access token. Subscribers re-render when this
    // changes (login, refresh-rotation, logout, cross-tab logout). Components
    // should prefer useAccessToken() (lib/auth-hooks.ts) over reading
    // localStorage directly.
    accessToken: string | null;
    status: AuthStatus;
    login: (email: string, password: string) => Promise<void>;
    register: (email: string, password: string, businessName: string, name?: string) => Promise<void>;
    logout: () => Promise<void>;
    setToken: (token: string) => void;
    refreshUser: () => Promise<void>;
    applyLoginResult: (res: {
        user_id: string;
        email: string;
        role: string;
        business_name?: string | null;
        minutes_remaining?: number;
        access_token?: string;
    }) => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
    const [user, setUser] = useState<MeResponse | null>(null);
    const [loading, setLoading] = useState(true);
    // Phase 2: token state owned by AuthContext (single writer). Initialised
    // synchronously on first render from the persisted store so the bootstrap
    // effect below sees the right value AND so the http-client's
    // setTokenProvider() callback below resolves immediately to the right
    // token rather than null-then-rotate.
    const [accessToken, setAccessTokenState] = useState<string | null>(() => {
        if (typeof window === "undefined") return null;
        return getBrowserAuthToken();
    });

    // Plumb the live token to the shared HTTP client. Every request reads
    // _externalTokenProvider() at request time, so a rotation here is
    // automatically picked up by all consumers without anyone re-reading
    // localStorage. Cleared on unmount so a unit test that tears down
    // AuthProvider doesn't leak state across tests.
    useEffect(() => {
        setTokenProvider(() => accessToken);
        return () => setTokenProvider(null);
    }, [accessToken]);

    // Cross-tab logout: when another tab clears the persisted token, sync
    // this tab's state. The "storage" event only fires for OTHER tabs'
    // writes — our own setBrowserAuthToken() doesn't echo through here.
    // Covers two cases: (a) explicit cross-tab logout, (b) external
    // tooling (e.g. DevTools clearing storage during a test).
    useEffect(() => {
        if (typeof window === "undefined") return;
        function onStorage(e: StorageEvent) {
            if (e.key !== "talklee.auth.token") return;
            const next = e.newValue && e.newValue.trim() ? e.newValue : null;
            setAccessTokenState(next);
            if (!next) setUser(null);
        }
        window.addEventListener("storage", onStorage);
        return () => window.removeEventListener("storage", onStorage);
    }, []);

    // Bootstrap auth state from the server.
    //
    // Skip the call when there's no legacy Bearer token AND no plausible
    // cookie auth signal — otherwise a cold visit fires /auth/me without
    // credentials, the http-client treats the 401 as session expiry, wipes
    // any in-flight localStorage write from a concurrent login, and tears
    // down sibling API calls (the connectors page's 10-second poll, the
    // dashboard query, etc.). The original gated behaviour is correct in
    // hybrid mode where cookies don't cross from api.talkleeai.com to
    // localhost. In pure cookie mode `document.cookie` reading won't see
    // httpOnly `talky_at`, but `talklee_auth_token` (legacy mirror) or any
    // first-party signal is enough to opt in.
    useEffect(() => {
        let cancelled = false;
        // After Phase 2 the state-initialised `accessToken` is already the
        // canonical answer — but for backwards compat (cookie-only sessions
        // where Bearer isn't set but talky_at cookie is) we still consult
        // the persistence layer once. Either signal opts us into the
        // /auth/me bootstrap.
        const legacyToken = accessToken ?? getBrowserAuthToken();
        if (!legacyToken) {
            // No auth signal — present a logged-out shell. Do NOT call
            // /auth/me or clear anything; the user will log in explicitly.
            setLoading(false);
            return;
        }

        // Retry once with backoff if /auth/me fails during a fresh-login
        // race. The login handler seeds `user` via flushSync BEFORE this
        // effect typically runs again, but if AuthProvider remounts (e.g.
        // a Suspense boundary swaps), this bootstrap fires fresh and can
        // race the cookie commit. One 1.5s retry inside the grace window
        // resolves >99% of those races; outside the window we fail open
        // and let the user re-login explicitly.
        async function loadMe(attempt: number): Promise<void> {
            try {
                const me = await api.getMe();
                if (!cancelled) setUser(me);
            } catch {
                if (cancelled) return;
                if (attempt === 0 && isWithinFreshLoginGrace()) {
                    if (process.env.NODE_ENV !== "production") {
                        console.debug("[auth] bootstrap /auth/me failed in grace window, retrying in 1500ms");
                    }
                    await new Promise((r) => setTimeout(r, 1500));
                    return loadMe(attempt + 1);
                }
                if (isWithinFreshLoginGrace()) {
                    // Still inside grace after the retry — keep whatever
                    // `user` state was seeded by applyLoginResult instead
                    // of nulling it. The next user-initiated request will
                    // re-validate via the cookie / bearer path.
                    if (process.env.NODE_ENV !== "production") {
                        console.debug("[auth] bootstrap /auth/me still failing in grace window — keeping seeded user state");
                    }
                    return;
                }
                // Real failure — http-client already cleared the token via
                // its 401 path; we just sync our user state.
                setUser(null);
            } finally {
                if (!cancelled) setLoading(false);
            }
        }
        void loadMe(0);
        return () => { cancelled = true; };
    }, []);

    // The ONE writer that touches the persisted token + the reactive state.
    // Every other action (login, register, logout, setToken, applyLoginResult)
    // delegates to this so localStorage and React state can't drift out of
    // sync. Marked `setAccessToken` (not `setAccessTokenState`) to remind
    // readers that this is the canonical mutation, not the raw setState.
    const setAccessToken = useCallback((token: string | null) => {
        setBrowserAuthToken(token);   // writes localStorage + legacy cookie mirror
        setAccessTokenState(token);   // updates reactive state → re-renders all subscribers
    }, []);

    const login = useCallback(async (email: string, password: string) => {
        const res = await api.login(email, password);
        setAccessToken(res.access_token);
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
    }, [setAccessToken]);

    const register = useCallback(async (
        email: string,
        password: string,
        businessName: string,
        name?: string,
    ) => {
        // /auth/register no longer issues a session: the user must verify
        // their email and then sign in via /auth/login. We resolve without
        // setting any auth state — callers should redirect to the "check
        // your email" screen after this resolves.
        //
        // The legacy session-on-register branch is preserved in case a
        // future backend reverts the behaviour, so this callback stays
        // forward-compatible.
        const res = await api.register(email, password, businessName, "basic", name);
        if (res.access_token && res.role) {
            setAccessToken(res.access_token);
            resetSessionExpiredLatch();
            setUser({
                id: res.user_id,
                email: res.email,
                role: res.role,
                business_name: res.business_name,
                minutes_remaining: res.minutes_remaining ?? 0,
            });
        }
    }, [setAccessToken]);

    const logout = useCallback(async () => {
        try {
            await api.logout();
        } catch {
            // Keep logout resilient for explicit user sign-out.
        } finally {
            // Always clear local state, regardless of whether the backend
            // call succeeded. Cross-tab tabs see this via the storage event
            // listener installed above and drop their own state.
            setAccessToken(null);
            try {
                localStorage.removeItem("refresh_token");
            } catch { /* ignore */ }
            setUser(null);
        }
    }, [setAccessToken]);

    const setToken = useCallback((token: string) => {
        setAccessToken(token);
        resetSessionExpiredLatch();
        // After setting token, try to load real user.
        // Inside the fresh-login grace window, a 401 here is a transient
        // race against cookie commit — keep the existing user state (if
        // any) rather than tearing it down and bouncing to /login.
        api.getMe()
            .then((me) => setUser(me))
            .catch(() => {
                if (isWithinFreshLoginGrace()) {
                    if (process.env.NODE_ENV !== "production") {
                        console.debug("[auth] setToken /auth/me failed in grace window — preserving state");
                    }
                    return;
                }
                setAccessToken(null);
                setUser(null);
            });
    }, [setAccessToken]);

    const refreshUser = useCallback(async () => {
        setLoading(true);
        try {
            const me = await api.getMe();
            setUser(me);
        } catch {
            if (isWithinFreshLoginGrace()) {
                if (process.env.NODE_ENV !== "production") {
                    console.debug("[auth] refreshUser failed in grace window — preserving state");
                }
                return;
            }
            setAccessToken(null);
            setUser(null);
        } finally {
            setLoading(false);
        }
    }, [setAccessToken]);

    // Synchronous user-state population from a login response. The login
    // POST returns enough fields to render the dashboard shell; we use
    // them directly so the redirect to /dashboard finds `user` already
    // populated and doesn't bounce back to /auth/login. /auth/me will
    // still run on next reload to refresh any drifted fields.
    const applyLoginResult = useCallback((res: {
        user_id: string;
        email: string;
        role: string;
        business_name?: string | null;
        minutes_remaining?: number;
        access_token?: string;
    }) => {
        // When a caller passes the access_token, we commit it through the
        // single writer so the new accessToken state stays in sync with
        // localStorage. Existing call sites (login-client.tsx) that still
        // call api.setToken() before applyLoginResult will work either
        // way — this setAccessToken is a no-op-equivalent if the writer
        // already ran with the same value.
        if (res.access_token) setAccessToken(res.access_token);
        setUser({
            id: res.user_id,
            email: res.email,
            role: res.role,
            business_name: res.business_name ?? undefined,
            minutes_remaining: res.minutes_remaining ?? 0,
        });
        setLoading(false);
    }, [setAccessToken]);

    // Compute the AuthStatus from the underlying state. Order matters:
    // `loading` wins over presence checks because a freshly-mounted
    // provider with a persisted token starts at status=loading while the
    // bootstrap /auth/me confirms the token is still valid.
    const status: AuthStatus = useMemo(() => {
        if (typeof window === "undefined") return "uninitialized";
        if (loading) return "loading";
        if (user && accessToken) return "authenticated";
        return "anonymous";
    }, [loading, user, accessToken]);

    const value = useMemo(
        () => ({ user, loading, accessToken, status, login, register, logout, setToken, refreshUser, applyLoginResult }),
        [loading, user, accessToken, status, login, register, logout, setToken, refreshUser, applyLoginResult],
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
    accessToken: null,
    status: "uninitialized",
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
    applyLoginResult: () => {
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
