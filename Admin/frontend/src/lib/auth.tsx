import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { api, type AdminUser, type LoginResponse } from './api';

const DUMMY_ADMIN_USER: AdminUser = { id: 'admin-001', email: 'admin@talky.ai', name: 'Admin User', role: 'super_admin' };
const USE_DUMMY_AUTH = import.meta.env.DEV && String(import.meta.env.VITE_USE_DUMMY_AUTH ?? '').toLowerCase() === 'true';
const TOKEN_KEY = 'admin_token';
const IDENTITY_KEY = 'talky.admin.identity.v1';
const FOREIGN_IDENTITY_KEY = 'talklee.auth.identity.v1';
const LOGOUT_PENDING_KEY = 'talky.admin.logout.pending';
const LOGOUT_UNCONFIRMED = 'Signed out of this Admin tab. Server sign-out could not be confirmed; your session may still be active elsewhere. Retrying signs out the account currently signed in in this browser.';

interface AuthState {
    user: AdminUser | null;
    isLoading: boolean;
    isAuthenticated: boolean;
    error: string | null;
    mfaChallenge: string | null;
}
interface AuthContextType extends AuthState {
    logoutRecoveryRequired: boolean;
    login: (email: string, password: string) => Promise<boolean>;
    verifyMfa: (value: string, recovery: boolean) => Promise<boolean>;
    cancelMfa: () => void;
    logout: () => Promise<void>;
    checkAuth: () => Promise<void>;
}
const AuthContext = createContext<AuthContextType | undefined>(undefined);
const anonymous = (error: string | null = null): AuthState => ({ user: null, isLoading: false, isAuthenticated: false, error, mfaChallenge: null });
function saved(key: string): string | null {
    try { return localStorage.getItem(key); } catch { return null; }
}
function save(key: string, value: string | null) {
    try { if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value); } catch { /* Cookie/in-memory authentication still works. */ }
}

export function AuthProvider({ children }: { children: ReactNode }) {
    const [state, setState] = useState<AuthState>({ ...anonymous(), isLoading: true });
    const authOperation = useRef(0);
    const attemptPending = useRef(false);
    const needsIdentityCheck = useRef(false);
    const channel = useRef<BroadcastChannel | null>(null);
    const challengeRef = useRef<string | null>(null);
    const [logoutRecoveryRequired, setLogoutRecoveryRequired] = useState(() => Boolean(saved(LOGOUT_PENDING_KEY)));
    const logoutPending = useRef(logoutRecoveryRequired);
    const setLogoutPending = (pending: boolean) => {
        logoutPending.current = pending;
        setLogoutRecoveryRequired(pending);
    };
    const beginOperation = () => {
        const operation = ++authOperation.current;
        let session = api.getAuthGeneration();
        return {
            current: () => operation === authOperation.current && session === api.getAuthGeneration(),
            adoptToken: (token: string | null) => {
                if (token === null) api.invalidateIdentity(); else api.setToken(token);
                session = api.getAuthGeneration();
            },
        };
    };
    const broadcast = (kind: 'changed' | 'logout') => {
        const marker = { kind, nonce: crypto.randomUUID() };
        save(IDENTITY_KEY, JSON.stringify(marker));
        try { channel.current?.postMessage(marker); } catch { /* Storage marker is the fallback. */ }
    };

    const checkAuth = async () => {
        if (attemptPending.current) return;
        try {
            const current = localStorage.getItem(TOKEN_KEY);
            if (current !== api.getToken()) api.setToken(current, false);
        } catch { /* Preserve the live in-memory token when storage is blocked. */ }
        const owner = beginOperation();
        if (USE_DUMMY_AUTH) { setState({ ...anonymous(), user: DUMMY_ADMIN_USER, isAuthenticated: true }); return; }
        if (logoutPending.current || saved(LOGOUT_PENDING_KEY)) {
            setLogoutPending(true); setState(anonymous(LOGOUT_UNCONFIRMED)); return;
        }
        // Cookie-only sessions are supported; /me proves the current authority.
        setState({ ...anonymous(), isLoading: true });
        try {
            const res = await api.verifyToken();
            if (!owner.current()) return;
            if (res.data?.valid && res.data.user) {
                setState({ ...anonymous(), user: res.data.user, isAuthenticated: true }); return;
            }
            const status = res.error?.status;
            if (res.error && (res.error.code === 'SESSION_RENEWAL_UNAVAILABLE' || (status !== 401 && status !== 403 && status !== 409))) {
                setState(anonymous(`Could not verify your session (${res.error.message}). Retry when the service is available.`)); return;
            }
            owner.adoptToken(null);
            setState(anonymous(status === 409 ? 'Your signed-in account changed. Sign in again to continue.' : null));
        } catch (caught) {
            if (owner.current()) setState(anonymous(caught instanceof Error ? caught.message : 'Session verification is unavailable.'));
        }
    };

    const finishAttempt = () => {
        attemptPending.current = false;
        if (needsIdentityCheck.current) { needsIdentityCheck.current = false; void checkAuth(); }
    };

    const completeLogin = async (result: LoginResponse, owner: ReturnType<typeof beginOperation>): Promise<boolean> => {
        if (!owner.current()) return false;
        if (!result.access_token || !result.user_id) { setState(anonymous('Sign-in could not be confirmed. Please start again.')); return false; }
        owner.adoptToken(result.access_token);
        api.expectIdentity({ userId: result.user_id, tenantId: result.tenant_id });
        const verified = await api.verifyToken();
        if (!owner.current()) return false;
        if (!verified.data?.valid || !verified.data.user || verified.data.user.id !== result.user_id) {
            if ([401, 403, 409].includes(verified.error?.status ?? 0)) owner.adoptToken(null);
            setState(anonymous(`Sign-in identity could not be verified. ${verified.error?.message ?? 'Please start again.'}`)); return false;
        }
        setLogoutPending(false); save(LOGOUT_PENDING_KEY, null); challengeRef.current = null;
        setState({ ...anonymous(), user: verified.data.user, isAuthenticated: true });
        broadcast('changed'); return true;
    };

    const login = async (email: string, password: string): Promise<boolean> => {
        if (attemptPending.current) return false;
        if (!email.trim() || !password) { setState(anonymous('Please enter email and password')); return false; }
        attemptPending.current = true;
        api.invalidateIdentity();
        const owner = beginOperation();
        challengeRef.current = null;
        setState({ ...anonymous(), isLoading: true });
        try {
            if (USE_DUMMY_AUTH) { setState({ ...anonymous(), user: { ...DUMMY_ADMIN_USER, email }, isAuthenticated: true }); return true; }
            const res = await api.login(email.trim(), password, owner.current);
            if (!owner.current()) return false;
            if (res.error || !res.data) { setState(anonymous(res.error?.message ?? 'Sign-in could not be confirmed.')); return false; }
            if (res.data.mfa_required) {
                if (!res.data.mfa_challenge_token) { setState(anonymous('The MFA challenge is missing. Please start sign-in again.')); return false; }
                challengeRef.current = res.data.mfa_challenge_token;
                setState({ ...anonymous(), mfaChallenge: res.data.mfa_challenge_token }); return false;
            }
            return await completeLogin(res.data, owner);
        } catch (caught) {
            if (owner.current()) setState(anonymous(caught instanceof Error ? caught.message : 'Sign-in could not be confirmed.'));
            return false;
        } finally { finishAttempt(); }
    };

    const verifyMfa = async (value: string, recovery: boolean): Promise<boolean> => {
        const challenge = challengeRef.current;
        if (!challenge || attemptPending.current) return false;
        const factor = value.trim();
        if ((recovery && (!factor || factor.length > 128)) || (!recovery && !/^\d{6}$/.test(factor))) {
            setState((before) => ({ ...before, error: recovery ? 'Enter the full recovery code.' : 'Enter a six-digit authenticator code.' })); return false;
        }
        attemptPending.current = true;
        const owner = beginOperation();
        setState((before) => ({ ...before, isLoading: true, error: null }));
        try {
            const res = await api.verifyMfaChallenge(challenge, recovery ? { recovery_code: factor } : { code: factor }, owner.current);
            if (!owner.current()) return false;
            if (res.error || !res.data) {
                // Lost success may have consumed a one-use challenge/code. Never replay automatically.
                const uncertain = !res.error?.sessionRequestNotSent
                    && (!res.error?.status || res.error.status >= 500 || res.error.code === 'INVALID_RESPONSE');
                if (uncertain) challengeRef.current = null;
                setState({ ...anonymous(uncertain
                    ? 'MFA completion could not be confirmed. Start sign-in again; a recovery code may already have been used.'
                    : res.error?.message ?? 'MFA verification failed. Use a new code or start sign-in again.'), mfaChallenge: uncertain ? null : challenge });
                return false;
            }
            challengeRef.current = null;
            return await completeLogin(res.data, owner);
        } catch {
            if (owner.current()) {
                challengeRef.current = null;
                setState(anonymous('MFA completion could not be confirmed. Start sign-in again; a recovery code may already have been used.'));
            }
            return false;
        } finally { finishAttempt(); }
    };
    const cancelMfa = () => {
        if (attemptPending.current) return;
        authOperation.current += 1; challengeRef.current = null; setState(anonymous());
    };

    const logout = async () => {
        if (attemptPending.current) return;
        attemptPending.current = true;
        const owner = beginOperation();
        // Capture credentials before masking local data. Admit no new login while
        // this response could still clear the browser's auth cookies.
        const request = USE_DUMMY_AUTH ? Promise.resolve({ data: {} }) : api.logout(owner.current);
        owner.adoptToken(null); api.expectIdentity(null); challengeRef.current = null;
        setLogoutPending(true); save(LOGOUT_PENDING_KEY, 'unconfirmed');
        setState({ ...anonymous(), isLoading: true }); broadcast('logout');
        try {
            const result = await request;
            if (!owner.current()) return;
            if ('error' in result && result.error) setState(anonymous(result.error.sessionRequestNotSent
                ? result.error.code === 'SESSION_COORDINATION_TIMEOUT'
                    ? 'Signed out of this Admin tab. Server sign-out was not sent because another browser session update is still running. Your server session may still be active. Wait for that update to finish, then retry. Retrying signs out the account currently signed in in this browser.'
                    : 'Signed out of this Admin tab. Server sign-out was not sent because browser session coordination is unavailable. Your server session may still be active. Restore session support in this browser, then retry. Retrying signs out the account currently signed in in this browser.'
                : LOGOUT_UNCONFIRMED));
            else { setLogoutPending(false); save(LOGOUT_PENDING_KEY, null); setState(anonymous()); }
        } catch { if (owner.current()) setState(anonymous(LOGOUT_UNCONFIRMED)); }
        finally { finishAttempt(); }
    };

    useEffect(() => api.onUnauthorized(() => {
        authOperation.current += 1; challengeRef.current = null;
        setState(anonymous('Your session has expired, changed, or was revoked. Please sign in again.'));
    }), []);
    useEffect(() => api.onAuthorityUnavailable((message) => {
        authOperation.current += 1; challengeRef.current = null;
        setState(anonymous(`Could not verify your current permissions (${message}). Retry when the service is available.`));
    }), []);
    useEffect(() => api.onSessionRefreshed((role, authorityChanged) => {
        if (authorityChanged) {
            authOperation.current += 1;
            setState({ ...anonymous(), isLoading: true });
            needsIdentityCheck.current = attemptPending.current;
            if (!attemptPending.current) void checkAuth();
        } else setState((before) => before.user ? { ...before, user: { ...before.user, role } } : before);
        // eslint-disable-next-line react-hooks/exhaustive-deps -- Reverification uses current API/ref authority.
    }), []);
    useEffect(() => {
        const seen = new Set<string>();
        const receive = (marker: unknown, foreign = false) => {
            if (!marker || typeof marker !== 'object') return;
            const value = marker as { kind?: unknown; nonce?: unknown };
            if (!['changed', 'logout'].includes(String(value.kind)) || typeof value.nonce !== 'string' || seen.has(value.nonce)) return;
            seen.add(value.nonce); if (seen.size > 100) seen.delete(seen.values().next().value!);
            authOperation.current += 1; challengeRef.current = null;
            // Another app has changed the shared cookies. Retire our persisted
            // bearer too, or checkAuth would reload it before cookie verification.
            // An own-app marker must preserve the sibling's newly stored bearer.
            api.invalidateIdentity(foreign);
            setLogoutPending(value.kind === 'logout');
            setState({ ...anonymous(), isLoading: value.kind === 'changed' });
            needsIdentityCheck.current = value.kind === 'changed' && attemptPending.current;
            if (value.kind === 'changed' && !attemptPending.current) void checkAuth();
        };
        const onStorage = (event: StorageEvent) => {
            if (event.key === TOKEN_KEY) {
                if (event.newValue === api.getToken()) return;
                authOperation.current += 1; challengeRef.current = null; api.setToken(event.newValue, false);
                setLogoutPending(!event.newValue);
                setState({ ...anonymous(), isLoading: Boolean(event.newValue) });
                needsIdentityCheck.current = Boolean(event.newValue) && attemptPending.current;
                if (event.newValue && !attemptPending.current) void checkAuth();
            } else if (event.key === IDENTITY_KEY || event.key === FOREIGN_IDENTITY_KEY) {
                try { receive(JSON.parse(event.newValue ?? 'null'), event.key === FOREIGN_IDENTITY_KEY); } catch { /* Ignore malformed markers. */ }
            } else if (event.key === null) receive({ kind: 'logout', nonce: crypto.randomUUID() });
        };
        window.addEventListener('storage', onStorage);
        let foreignChannel: BroadcastChannel | null = null;
        if (typeof window.BroadcastChannel === 'function') {
            try {
                channel.current = new window.BroadcastChannel(IDENTITY_KEY);
                channel.current.onmessage = (event) => receive(event.data);
            } catch { /* Storage markers remain available when channels are denied. */ }
            try {
                foreignChannel = new window.BroadcastChannel(FOREIGN_IDENTITY_KEY);
                foreignChannel.onmessage = (event) => receive(event.data, true);
            } catch { /* Do not relay foreign markers or copy another app's bearer. */ }
        }
        void checkAuth();
        return () => {
            authOperation.current += 1; window.removeEventListener('storage', onStorage);
            channel.current?.close(); channel.current = null;
            foreignChannel?.close();
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps -- Initial subscriptions use API/ref ownership, never captured user authority.
    }, []);

    return <AuthContext.Provider value={{ ...state, logoutRecoveryRequired, login, verifyMfa, cancelMfa, logout, checkAuth }}>{children}</AuthContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components -- Provider and consumer share the same context.
export function useAuth() {
    const context = useContext(AuthContext);
    if (context === undefined) throw new Error('useAuth must be used within an AuthProvider');
    return context;
}
