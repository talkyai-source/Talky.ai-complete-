"use client";

import React, { createContext, useContext, useEffect, useMemo, useState, ReactNode, useCallback } from "react";
import { api } from "@/lib/api";
import { clearFreshLoginGrace, resetSessionExpiredLatch, isWithinFreshLoginGrace, setTokenProvider, isApiClientError } from "@/lib/http-client";
import { consumeLegacyAuthCookie, getBrowserAuthToken, isBearerFallbackEnabled, setBrowserAuthToken } from "@/lib/auth-token";
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
    refreshUser: (opts?: { silent?: boolean }) => Promise<void>;
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
    //
    // Phase 7 migration shim: if the canonical localStorage key is empty
    // but the user has a legacy `talklee_auth_token` cookie from a
    // pre-Phase-7 session, consume the cookie value once. consumeLegacy…
    // clears the cookie atomically with the read, so this is single-shot.
    // The fall-through `setBrowserAuthToken(migrated)` commits the value
    // into the canonical store for all subsequent renders / tabs.
    // REMOVE AFTER 2026-06-03 (2-week soak after Phase 7 deploy).
    const [accessToken, setAccessTokenState] = useState<string | null>(() => {
        if (typeof window === "undefined") return null;
        const persisted = getBrowserAuthToken();
        if (persisted) {
            // Vuln-fix 2026-05-21: when the Bearer fallback is disabled
            // by env, any token sitting in localStorage is a leftover
            // from a session that logged in BEFORE the flag flipped. We
            // want it out of localStorage immediately rather than
            // waiting 7 days for natural rotation — the JWT in storage
            // is the XSS attack surface the flag was meant to close.
            //
            // Wipe it from storage (one-shot, idempotent) but RETURN
            // the value so the in-memory session continues normally.
            // The user stays logged in via cookies; only the
            // exfiltratable copy goes away. Next page load: no
            // persisted token, AuthContext bootstraps via the
            // /auth/me cookie path.
            if (!isBearerFallbackEnabled()) {
                try {
                    window.localStorage.removeItem("talklee.auth.token");
                } catch { /* ignore */ }
            }
            return persisted;
        }
        const migrated = consumeLegacyAuthCookie();
        if (migrated) {
            setBrowserAuthToken(migrated);
            return migrated;
        }
        return null;
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

    // AH-Phase-E: retry a previously-failed logout on mount. If the user
    // clicked Sign Out and the backend call failed (offline, 5xx,
    // timeout), AuthContext.logout queued a `talky.logout.pending`
    // localStorage flag. Re-fire api.logout silently — the server
    // invalidates the refresh_tokens row, clearing the cookies, and
    // narrows the window where a leaked JWT could still be used.
    // Runs once per mount, before the /auth/me bootstrap.
    useEffect(() => {
        if (typeof window === "undefined") return;
        let pending: string | null = null;
        try { pending = window.localStorage.getItem("talky.logout.pending"); } catch { /* ignore */ }
        if (!pending) return;
        let cancelled = false;
        (async () => {
            try {
                await api.logout();
                if (cancelled) return;
                try { window.localStorage.removeItem("talky.logout.pending"); } catch { /* ignore */ }
                if (process.env.NODE_ENV !== "production") {
                    console.debug("[auth] retried pending logout, server confirmed");
                }
            } catch (err) {
                if (cancelled) return;
                if (isApiClientError(err) && (err.status === 401 || err.status === 403)) {
                    // Server already considers us logged out — success.
                    try { window.localStorage.removeItem("talky.logout.pending"); } catch { /* ignore */ }
                } else {
                    // Still failing; leave the flag for the next mount.
                    if (process.env.NODE_ENV !== "production") {
                        console.debug("[auth] pending logout retry still failing — will retry on next mount");
                    }
                }
            }
        })();
        return () => { cancelled = true; };
    }, []);

    // Bootstrap auth state from the server.
    //
    // Two gating strategies depending on the deploy mode:
    //
    //   Bearer-fallback ON (default): skip /auth/me when there's no Bearer
    //   token in localStorage. The Bearer's presence is the proxy signal
    //   that "we have a session." Without it, calling /auth/me on a cold
    //   anonymous visit fires a 401 → trips the session-expired latch →
    //   tears down sibling API calls. The protective behaviour we want
    //   for visitors who land on /auth/login without a session.
    //
    //   Bearer-fallback OFF (Phase F+F2 cookie-only mode): the JWT is
    //   never written to localStorage anymore. The HttpOnly `talky_at`
    //   cookie IS the session and JS can't read it. So we MUST call
    //   /auth/me unconditionally on mount — if the cookie's valid the
    //   server returns the user; if not we get a 401 and present a
    //   logged-out shell. The latch-tearing-down concern is mitigated
    //   by the fresh-login grace window and by the fact that nothing
    //   in localStorage is at risk of being wiped anymore.
    //
    // Hotfix 2026-05-21: the previous gate only checked the Bearer path,
    // so after the Phase F flip every page reload bounced the user to
    // /auth/login even with a valid cookie. The branch on
    // isBearerFallbackEnabled() restores the correct cookie-mode
    // behaviour.
    useEffect(() => {
        let cancelled = false;
        const legacyToken = accessToken ?? getBrowserAuthToken();
        const inCookieOnlyMode = !isBearerFallbackEnabled();
        if (!legacyToken && !inCookieOnlyMode) {
            // Bearer mode + no Bearer = anonymous cold visit. Don't
            // call /auth/me, just present the logged-out shell.
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
    //
    // Phase 7: setBrowserAuthToken now writes ONLY the canonical
    // localStorage key. The legacy `talklee_auth_token` cookie mirror
    // was removed — it was non-HttpOnly pure attack surface with the
    // shared client + AuthContext fallback path doing its job.
    const setAccessToken = useCallback((token: string | null) => {
        setBrowserAuthToken(token);   // writes localStorage (canonical, single key)
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
        // AH-Phase-E: distinguish "logout actually invalidated the
        // server-side session" from "logout call failed and the row may
        // still be alive". On failure (network drop, 5xx) we set a
        // localStorage flag so the NEXT page load (or AuthProvider mount)
        // retries silently. 401/403 from the backend means the server
        // already considers us logged out — that's a success state and
        // the flag is cleared.
        let serverConfirmedLogout = false;
        try {
            await api.logout();
            serverConfirmedLogout = true;
        } catch (err) {
            if (isApiClientError(err) && (err.status === 401 || err.status === 403)) {
                // Server already considers this session gone. Treat as success.
                serverConfirmedLogout = true;
            } else {
                serverConfirmedLogout = false;
                if (process.env.NODE_ENV !== "production") {
                    console.warn("[auth] logout call failed; queued for retry on next mount", err);
                }
            }
        } finally {
            // Always clear local state, regardless of whether the backend
            // call succeeded. Cross-tab tabs see this via the storage event
            // listener installed above and drop their own state.
            //
            // Phase 7: also scrub the legacy `refresh_token` localStorage
            // key on logout. Nothing in the app writes it anymore (Phase 7
            // dropped the login-success + oauth-callback writes), but any
            // user who logged in before Phase 7 still has a stale value
            // sitting in their browser. One defensive removeItem on the
            // next logout clears it across the user base; this whole try
            // block can be deleted after the 2-week soak.
            setAccessToken(null);
            // Phase E: kill the fresh-login grace window. If the user
            // logged in and out within 15s, a lingering grace would
            // suppress an otherwise-correct session-expired bounce on the
            // next API call. Logout is the explicit signal that "any
            // recent login is no longer in effect."
            clearFreshLoginGrace();
            try {
                localStorage.removeItem("refresh_token");
                if (serverConfirmedLogout) {
                    localStorage.removeItem("talky.logout.pending");
                } else {
                    // Queue the retry. Bootstrap effect on next mount
                    // (or a focus-driven retry) will re-fire api.logout
                    // until the server confirms.
                    localStorage.setItem("talky.logout.pending", String(Date.now()));
                }
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

    // `silent: true` callers (Phase 3 suspension-freshness loop, any future
    // background poll) MUST NOT flip authLoading or tear down auth state
    // on transient failures. The 30-second loop fires every visibility
    // change too — a single network blip in any of those would otherwise
    // bounce the user back to /login.
    //
    // The tear-down branch is now gated on `error.code === "unauthorized"`
    // explicitly, so timeouts, 5xx, and network errors all preserve the
    // current session. Real 401s still log the user out — those mean the
    // server has actually invalidated the session.
    const refreshUser = useCallback(async (opts?: { silent?: boolean }) => {
        const silent = opts?.silent === true;
        if (!silent) setLoading(true);
        try {
            const me = await api.getMe();
            setUser(me);
        } catch (err) {
            if (isWithinFreshLoginGrace()) {
                if (process.env.NODE_ENV !== "production") {
                    console.debug("[auth] refreshUser failed in grace window — preserving state");
                }
                return;
            }
            // Only tear down auth state on confirmed unauthorized. Anything
            // else (network blip, 5xx, timeout, the new heavier /auth/me
            // query running slow on a cold Vercel function) keeps the
            // current session intact — the next tick or user action gets
            // another chance to refresh.
            const isAuthFailure = isApiClientError(err) && err.code === "unauthorized";
            if (!isAuthFailure) {
                if (process.env.NODE_ENV !== "production") {
                    console.debug(
                        "[auth] refreshUser non-auth failure — preserving state",
                        err,
                    );
                }
                return;
            }
            setAccessToken(null);
            setUser(null);
        } finally {
            if (!silent) setLoading(false);
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
        // localStorage. Existing call sites (login-client.tsx) still call
        // api.setToken() BEFORE applyLoginResult — that path writes
        // localStorage but doesn't touch this reactive state. Fall back
        // to a localStorage read so the reactive accessToken catches up
        // even when the caller didn't pass the field explicitly. Phase 5
        // will eliminate the legacy api.setToken path entirely.
        const tokenToCommit = res.access_token ?? getBrowserAuthToken();
        if (tokenToCommit) {
            // Skip the localStorage write if the value is already there —
            // setAccessToken's writer is idempotent but we want to avoid
            // unnecessary cookie-mirror writes in the legacy fallback case.
            setAccessTokenState(tokenToCommit);
            if (res.access_token) setBrowserAuthToken(tokenToCommit);
        }
        setUser({
            id: res.user_id,
            email: res.email,
            role: res.role,
            business_name: res.business_name ?? undefined,
            minutes_remaining: res.minutes_remaining ?? 0,
        });
        setLoading(false);
    }, []);

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
