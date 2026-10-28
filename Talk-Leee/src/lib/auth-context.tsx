"use client";

import { normalizeMinutesAllowance, type MinutesState } from "@/lib/minutes-allowance";
import React, { createContext, useContext, useEffect, useMemo, useRef, useState, ReactNode, useCallback } from "react";
import { api } from "@/lib/api";
import { clearFreshLoginGrace, resetSessionExpiredLatch, isWithinFreshLoginGrace, setTokenProvider, isApiClientError, setRequestIdentity, invalidateRequestIdentity, ApiClientError } from "@/lib/http-client";
import { consumeLegacyAuthCookie, getBrowserAuthToken, isBearerFallbackEnabled, setBrowserAuthToken } from "@/lib/auth-token";
import { notificationsStore } from "@/lib/notifications";

// Contains no token or account data. Cookie-only sessions also need an explicit
// cross-tab boundary because removing an absent bearer key emits no event.
export const AUTH_IDENTITY_EVENT_KEY = "talklee.auth.identity.v1";
const FOREIGN_IDENTITY_EVENT_KEY = "talky.admin.identity.v1";
function publishIdentityChange(kind: "changed" | "logout", channel: BroadcastChannel | null) {
    const message = { kind, nonce: crypto.randomUUID() };
    let delivered = false;
    try { window.localStorage.setItem(AUTH_IDENTITY_EVENT_KEY, JSON.stringify(message)); delivered = true; } catch { /* BroadcastChannel can still notify cookie-only tabs. */ }
    try { if (channel) { channel.postMessage(message); delivered = true; } } catch { /* Fail closed below if neither transport works. */ }
    return delivered;
}
function clearUnconfirmedLogout() {
    try { window.localStorage.removeItem("talky.logout.pending"); } catch { /* Storage may be unavailable. */ }
}
interface MeResponse {
    id: string;
    email: string;
    name?: string;
    business_name?: string;
    role: string;
    minutes_remaining: number | null;
    minutes_state?: MinutesState;
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
    notificationSyncAvailable: boolean;
    login: (email: string, password: string) => Promise<void>;
    logout: () => Promise<{ serverConfirmed: boolean; identityCurrent: boolean }>;
    setToken: (token: string) => void;
    refreshUser: (opts?: { silent?: boolean }) => Promise<void>;
    applyLoginResult: (res: {
        user_id: string;
        tenant_id?: string;
        email: string;
        role: string;
        business_name?: string | null;
        minutes_remaining?: number | null;
        minutes_state?: MinutesState;
        access_token?: string;
    }) => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
    const [user, setUserState] = useState<MeResponse | null>(null);
    const setUser = useCallback((next: MeResponse | null) => {
        // Optimistic legacy login payloads can omit tenant identity. Only /me
        // may complete that scope; a platform administrator can be tenantless.
        setRequestIdentity(next && (next.tenant_id || next.role === "platform_admin")
            ? { userId: next.id, tenantId: next.tenant_id } : null);
        setUserState(next);
    }, []);
    const [loading, setLoading] = useState(true);
    const [notificationSyncAvailable, setNotificationSyncAvailable] = useState(false);
    const identityChannel = useRef<BroadcastChannel | null>(null);
    const broadcastIdentityChange = useCallback((kind: "changed" | "logout") => {
        if (!publishIdentityChange(kind, identityChannel.current)) {
            notificationsStore.setIdentity(null);
            setNotificationSyncAvailable(false);
        }
    }, []);
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

    // Latest-value mirror of `accessToken` for effects that must NOT re-run
    // when the token rotates (see the bootstrap effect below).
    const accessTokenRef = useRef(accessToken);
    const identityEpoch = useRef(0);
    const profileRead = useRef(0);
    // Local intent must survive in-flight logout and unavailable persistence.
    // Only an owned successful sign-in may reopen this mounted provider.
    const logoutIntent = useRef(false);
    // Explicit logout closes admission as well as invalidating earlier reads:
    // a timer that starts afterward must not rediscover the still-valid cookie
    // while the logout request is in flight.
    const profileReadsAllowed = useRef(true);
    const invalidateIdentity = useCallback(() => {
        invalidateRequestIdentity();
        profileReadsAllowed.current = false;
        identityEpoch.current += 1;
        profileRead.current += 1;
        notificationsStore.setIdentity(null);
        return identityEpoch.current;
    }, []);

    // Plumb the live token to the shared HTTP client. Every request reads
    // _externalTokenProvider() at request time, so a rotation here is
    // automatically picked up by all consumers without anyone re-reading
    // localStorage. Cleared on unmount so a unit test that tears down
    // AuthProvider doesn't leak state across tests.
    useEffect(() => {
        profileReadsAllowed.current = true;
        setTokenProvider(() => accessTokenRef.current, token => {
            accessTokenRef.current = token;
            setBrowserAuthToken(token);
            setAccessTokenState(token);
        });
        return () => {
            profileReadsAllowed.current = false;
            identityEpoch.current += 1;
            profileRead.current += 1;
            setTokenProvider(null);
        };
    }, []);

    // Cross-tab logout: when another tab clears the persisted token, sync
    // this tab's state. The "storage" event only fires for OTHER tabs'
    // writes — our own setBrowserAuthToken() doesn't echo through here.
    // Covers two cases: (a) explicit cross-tab logout, (b) external
    // tooling (e.g. DevTools clearing storage during a test).
    useEffect(() => {
        if (typeof window === "undefined") return;
        const seen = new Set<string>();
        function handleIdentityChange(kind: "changed" | "logout") {
            invalidateRequestIdentity();
            setUser(null);
            const epoch = ++identityEpoch.current;
            const read = ++profileRead.current;
            try {
                if (window.localStorage.getItem("talky.logout.pending")) logoutIntent.current = true;
            } catch { /* Keep the volatile intent when storage is unavailable. */ }
            // A notification is not confirmation of an earlier local logout.
            // Only an explicit successful sign-in clears that durable fence.
            if (kind === "logout" || logoutIntent.current) {
                profileReadsAllowed.current = false;
                notificationsStore.setIdentity(null);
                accessTokenRef.current = null;
                setAccessTokenState(null);
                setUser(null);
                setLoading(false);
                clearFreshLoginGrace();
                return;
            }
            // An unverified token/session change masks old data immediately.
            // Keep the saved same-identity history until /me proves the scope.
            notificationsStore.suspendIdentity();
            profileReadsAllowed.current = true;
            // The old user is masked above but the session is NOT known to be
            // gone: a sibling tab's routine token rotation lands here too. Hold
            // `loading` until /me answers so DashboardLayout's `!loading && !user`
            // redirect cannot send a still-valid session to /auth/login.
            setLoading(true);
            resetSessionExpiredLatch();
            const current = () => identityEpoch.current === epoch && profileRead.current === read;
            void api.getMe().then(me => {
                if (current()) { setUser(me); setLoading(false); }
            }).catch(() => {
                if (current()) { notificationsStore.setIdentity(null); setUser(null); setLoading(false); }
            });
        }
        function receiveMarker(value: unknown, foreign = false) {
            if (!value || typeof value !== "object") return;
            const message = value as { kind?: unknown; nonce?: unknown };
            if ((message.kind !== "changed" && message.kind !== "logout") || typeof message.nonce !== "string" || !message.nonce || message.nonce.length > 200) return;
            if (seen.has(message.nonce)) return;
            seen.add(message.nonce);
            if (seen.size > 100) seen.delete(seen.values().next().value!);
            if (foreign) {
                // Foreign cookie changes invalidate this app's old bearer; it
                // must not accompany the canonical cookie-only identity check.
                accessTokenRef.current = null;
                setAccessTokenState(null);
                setBrowserAuthToken(null);
            } else if (message.kind === "changed") {
                accessTokenRef.current = getBrowserAuthToken();
                setAccessTokenState(accessTokenRef.current);
            }
            handleIdentityChange(message.kind);
        }
        function onStorage(e: StorageEvent) {
            if (e.key === "talklee.auth.token") {
                const next = e.newValue && e.newValue.trim() ? e.newValue : null;
                if (next === accessTokenRef.current) return;
                accessTokenRef.current = next;
                setAccessTokenState(next);
                handleIdentityChange(next ? "changed" : "logout");
            } else if (e.key === AUTH_IDENTITY_EVENT_KEY || e.key === FOREIGN_IDENTITY_EVENT_KEY) {
                try { receiveMarker(JSON.parse(e.newValue ?? "null"), e.key === FOREIGN_IDENTITY_EVENT_KEY); } catch { /* Ignore malformed markers. */ }
            } else if (e.key === null) handleIdentityChange("logout");
        }
        let storageAvailable = false;
        try {
            const probe = `${AUTH_IDENTITY_EVENT_KEY}.probe`;
            window.localStorage.setItem(probe, "1"); window.localStorage.removeItem(probe);
            storageAvailable = true;
        } catch { /* Storage can be blocked or full while cookies remain usable. */ }
        let channel: BroadcastChannel | null = null;
        let foreignChannel: BroadcastChannel | null = null;
        try {
            if (typeof window.BroadcastChannel !== "undefined") {
                channel = new window.BroadcastChannel(AUTH_IDENTITY_EVENT_KEY);
                channel.onmessage = event => receiveMarker(event.data);
            }
        } catch { /* Notifications remain unscoped if both transports fail. */ }
        try {
            if (typeof window.BroadcastChannel !== "undefined") {
                foreignChannel = new window.BroadcastChannel(FOREIGN_IDENTITY_EVENT_KEY);
                foreignChannel.onmessage = event => receiveMarker(event.data, true);
            }
        } catch { /* The existing foreign storage marker remains the fallback. */ }
        identityChannel.current = channel;
        // Capability depends on successfully opening browser transports, which
        // must happen after mount rather than during render or on the server.
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setNotificationSyncAvailable(storageAvailable || !!channel);
        window.addEventListener("storage", onStorage);
        return () => {
            window.removeEventListener("storage", onStorage);
            identityChannel.current = null;
            channel?.close();
            foreignChannel?.close();
        };
    }, [setUser]);

    // A failed logout has an unknown server outcome. Do not replay a timestamp
    // against current cookies: they may now belong to a different session.

    // Verify the session on mount using existing cookies and any saved bearer.
    // A missing bearer says nothing about the HttpOnly cookie. getMe suppresses
    // the global expiry redirect; an anonymous response settles this provider.
    // Token changes stay outside the dependency list so rotation cannot restart
    // bootstrap. The HTTP client reads the live token ref when needed.
    useEffect(() => {
        let cancelled = false;
        const epoch = identityEpoch.current;
        const read = ++profileRead.current;
        const current = () => !cancelled && identityEpoch.current === epoch && profileRead.current === read;
        try {
            if (window.localStorage.getItem("talky.logout.pending")) {
                logoutIntent.current = true;
                profileReadsAllowed.current = false;
                // Settle the client-only bootstrap after reading its persisted receipt.
                // eslint-disable-next-line react-hooks/set-state-in-effect
                setLoading(false);
                return;
            }
        } catch { /* Continue the usual bootstrap when storage is unavailable. */ }
        // Retain the existing single retry during fresh-login grace. Current
        // ownership checks reject reads superseded by logout or another login.
        async function loadMe(attempt: number): Promise<void> {
            if (!current()) return;
            try {
                const me = await api.getMe();
                if (current()) setUser(me);
            } catch {
                if (!current()) return;
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
                // The canonical check failed; leave this provider anonymous.
                setUser(null);
            } finally {
                if (current()) setLoading(false);
            }
        }
        void loadMe(0);
        return () => { cancelled = true; };
    }, [setUser]);

    // The ONE writer that touches the persisted token + the reactive state.
    // Every other action (login, logout, setToken, applyLoginResult)
    // delegates to this so localStorage and React state can't drift out of
    // sync. Marked `setAccessToken` (not `setAccessTokenState`) to remind
    // readers that this is the canonical mutation, not the raw setState.
    //
    // Phase 7: setBrowserAuthToken now writes ONLY the canonical
    // localStorage key. The legacy `talklee_auth_token` cookie mirror
    // was removed — it was non-HttpOnly pure attack surface with the
    // shared client + AuthContext fallback path doing its job.
    const setAccessToken = useCallback((token: string | null) => {
        accessTokenRef.current = token;
        setBrowserAuthToken(token);   // writes localStorage (canonical, single key)
        setAccessTokenState(token);   // updates reactive state → re-renders all subscribers
    }, []);

    const login = useCallback(async (email: string, password: string) => {
        const epoch = invalidateIdentity();
        const res = await api.login(email, password);
        if (identityEpoch.current !== epoch) return;
        if (res.mfa_required || !res.access_token) {
            throw new ApiClientError({ code: "mfa_required", message: "Complete two-factor authentication on the sign-in page.",
                url: "/auth/login", method: "POST" });
        }
        logoutIntent.current = false;
        clearUnconfirmedLogout();
        profileReadsAllowed.current = true;
        profileRead.current += 1;
        setAccessToken(res.access_token);
        // Re-arm the http-client's session-expired latch so the NEXT 401
        // (after this fresh session eventually expires) fires the redirect
        // again.  Without this, a logout → login round-trip would leave
        // the latch tripped and the next expiry would silently no-op.
        resetSessionExpiredLatch();
        setUser({
            id: res.user_id,
            tenant_id: res.tenant_id,
            email: res.email,
            role: res.role,
            business_name: res.business_name,
            ...normalizeMinutesAllowance(res),
        });
        setLoading(false);
        broadcastIdentityChange("changed");
    }, [setAccessToken, invalidateIdentity, broadcastIdentityChange, setUser]);

    const logout = useCallback(async () => {
        const expectedIdentity = user ? { userId: user.id, tenantId: user.tenant_id } : undefined;
        logoutIntent.current = true;
        // Persist before a cross-app hint can retire this operation's epoch.
        // A failed write still leaves the mounted provider fenced in memory.
        try { window.localStorage.setItem("talky.logout.pending", String(Date.now())); } catch { /* Keep volatile intent. */ }
        const epoch = invalidateIdentity();
        setUser(null);
        setLoading(false);
        clearFreshLoginGrace();
        broadcastIdentityChange("logout");
        // Keep an unconfirmed outcome visible; never replay it against a
        // future session. Only a successful acknowledgment confirms revocation.
        let serverConfirmedLogout = false;
        try {
            await api.logout(expectedIdentity);
            serverConfirmedLogout = true;
        } catch (err) {
            serverConfirmedLogout = false;
            if (process.env.NODE_ENV !== "production") {
                console.warn("[auth] local sign-out completed; server revocation is unconfirmed", err);
            }
        } finally {
            // A later login owns the current local identity. This older logout
            // completion must not erase its profile or notification scope.
            if (identityEpoch.current === epoch) {
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
                        // Preserve uncertainty without replaying an unbound logout.
                        localStorage.setItem("talky.logout.pending", String(Date.now()));
                    }
                } catch { /* ignore */ }
                setUser(null);
            }
        }
        return { serverConfirmed: serverConfirmedLogout, identityCurrent: identityEpoch.current === epoch };
    }, [setAccessToken, invalidateIdentity, broadcastIdentityChange, setUser, user]);

    const setToken = useCallback((token: string) => {
        const epoch = invalidateIdentity();
        profileReadsAllowed.current = true;
        const read = ++profileRead.current;
        const current = () => identityEpoch.current === epoch && profileRead.current === read;
        setAccessToken(token);
        setUser(null);
        setLoading(true);
        broadcastIdentityChange("changed");
        resetSessionExpiredLatch();
        // After setting token, try to load real user.
        // Inside the fresh-login grace window, a 401 here is a transient
        // race against cookie commit — keep the existing user state (if
        // any) rather than tearing it down and bouncing to /login.
        api.getMe()
            .then((me) => { if (current()) setUser(me); })
            .catch(() => {
                if (!current()) return;
                if (isWithinFreshLoginGrace()) {
                    if (process.env.NODE_ENV !== "production") {
                        console.debug("[auth] setToken /auth/me failed in grace window — preserving state");
                    }
                    return;
                }
                setAccessToken(null);
                setUser(null);
            }).finally(() => { if (current()) setLoading(false); });
    }, [setAccessToken, invalidateIdentity, broadcastIdentityChange, setUser]);

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
        if (!profileReadsAllowed.current) return;
        const epoch = identityEpoch.current;
        const read = ++profileRead.current;
        const current = () => identityEpoch.current === epoch && profileRead.current === read;
        const silent = opts?.silent === true;
        if (!silent) setLoading(true);
        try {
            const me = await api.getMe();
            if (current()) setUser(me);
        } catch (err) {
            if (!current()) return;
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
            invalidateIdentity();
            setUser(null);
            setLoading(false);
        } finally {
            // A newer silent read can supersede a foreground read that opened
            // the loading state. The winning read settles that state too.
            if (current()) setLoading(false);
        }
    }, [setAccessToken, invalidateIdentity, setUser]);

    // Synchronous user-state population from a login response. The login
    // POST returns enough fields to render the dashboard shell; we use
    // them directly so the redirect to /dashboard finds `user` already
    // populated and doesn't bounce back to /auth/login. /auth/me will
    // still run on next reload to refresh any drifted fields.
    const applyLoginResult = useCallback((res: {
        user_id: string;
        tenant_id?: string;
        email: string;
        role: string;
        business_name?: string | null;
        minutes_remaining?: number | null;
        minutes_state?: MinutesState;
        access_token?: string;
    }) => {
        invalidateIdentity();
        logoutIntent.current = false;
        clearUnconfirmedLogout();
        profileReadsAllowed.current = true;
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
            accessTokenRef.current = tokenToCommit;
            setAccessTokenState(tokenToCommit);
            if (res.access_token) setBrowserAuthToken(tokenToCommit);
        }
        setUser({
            id: res.user_id,
            tenant_id: res.tenant_id,
            email: res.email,
            role: res.role,
            business_name: res.business_name ?? undefined,
            ...normalizeMinutesAllowance(res),
        });
        setLoading(false);
        broadcastIdentityChange("changed");
    }, [invalidateIdentity, broadcastIdentityChange, setUser]);

    // Compute the AuthStatus from the underlying state. Order matters:
    // `loading` wins over presence checks because a freshly-mounted
    // provider with a persisted token starts at status=loading while the
    // bootstrap /auth/me confirms the token is still valid.
    const status: AuthStatus = useMemo(() => {
        if (typeof window === "undefined") return "uninitialized";
        if (loading) return "loading";
        // In cookie-only mode (Phase F+F2), the JWT lives only in the HttpOnly
        // `talky_at` cookie — JS can't read it, so accessToken stays null even
        // for a fully-logged-in user. The `user` object is populated by a
        // successful /auth/me, which is the real proof of an active session
        // regardless of transport. Gating on `user && accessToken` made every
        // cookie-only user look anonymous and bounced features like Ask AI
        // back to /auth/login on click.
        if (user) return "authenticated";
        return "anonymous";
        // `accessToken` is deliberately NOT a dependency: since the cookie-only
        // fix above, the status is derived from `loading` + `user` alone, so
        // listing the token only made this memo churn on every rotation.
    }, [loading, user]);

    const value = useMemo(
        () => ({ user, loading, accessToken, status, notificationSyncAvailable, login, logout, setToken, refreshUser, applyLoginResult }),
        [loading, user, accessToken, status, notificationSyncAvailable, login, logout, setToken, refreshUser, applyLoginResult],
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
    notificationSyncAvailable: false,
    login: async () => {
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
