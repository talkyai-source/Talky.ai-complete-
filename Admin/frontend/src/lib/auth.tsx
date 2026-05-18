import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { api, type AdminUser } from './api';

// Dummy admin user used only when VITE_USE_DUMMY_AUTH=true (dev / Storybook).
const DUMMY_ADMIN_USER: AdminUser = {
    id: 'admin-001',
    email: 'admin@talky.ai',
    name: 'Admin User',
    role: 'super_admin',
};

// Dummy-auth gate is now env-driven instead of hardcoded.
//
// Production: leave VITE_USE_DUMMY_AUTH unset (or "false") so the real
// /auth/login + /auth/verify endpoints back the AuthProvider.
//
// Dev: set VITE_USE_DUMMY_AUTH=true in `.env.local` to bypass auth and
// always log in as DUMMY_ADMIN_USER.
const USE_DUMMY_AUTH =
    String(import.meta.env.VITE_USE_DUMMY_AUTH ?? '').toLowerCase() === 'true';

interface AuthState {
    user: AdminUser | null;
    isLoading: boolean;
    isAuthenticated: boolean;
    error: string | null;
}

interface AuthContextType extends AuthState {
    login: (email: string, password: string) => Promise<boolean>;
    logout: () => Promise<void>;
    checkAuth: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

const TOKEN_KEY = 'admin_token';

export function AuthProvider({ children }: { children: ReactNode }) {
    const [state, setState] = useState<AuthState>({
        user: null,
        isLoading: true,
        isAuthenticated: false,
        error: null,
    });

    const checkAuth = async () => {
        if (USE_DUMMY_AUTH) {
            await new Promise((resolve) => setTimeout(resolve, 300));
            setState({
                user: DUMMY_ADMIN_USER,
                isLoading: false,
                isAuthenticated: true,
                error: null,
            });
            return;
        }

        const token = localStorage.getItem(TOKEN_KEY);
        if (!token) {
            setState({
                user: null,
                isLoading: false,
                isAuthenticated: false,
                error: null,
            });
            return;
        }

        try {
            const res = await api.verifyToken();
            if (res.data?.valid && res.data.user) {
                setState({
                    user: res.data.user,
                    isLoading: false,
                    isAuthenticated: true,
                    error: null,
                });
                return;
            }
            // Token rejected — clear it.
            localStorage.removeItem(TOKEN_KEY);
            setState({
                user: null,
                isLoading: false,
                isAuthenticated: false,
                error: null,
            });
        } catch (err) {
            // Backend unreachable — keep the user out rather than silently
            // logging them in.
            setState({
                user: null,
                isLoading: false,
                isAuthenticated: false,
                error: err instanceof Error ? err.message : 'Auth check failed',
            });
        }
    };

    const login = async (email: string, password: string): Promise<boolean> => {
        setState((prev) => ({ ...prev, isLoading: true, error: null }));

        if (USE_DUMMY_AUTH) {
            await new Promise((resolve) => setTimeout(resolve, 500));
            if (email && password) {
                setState({
                    user: { ...DUMMY_ADMIN_USER, email },
                    isLoading: false,
                    isAuthenticated: true,
                    error: null,
                });
                return true;
            }
            setState((prev) => ({
                ...prev,
                isLoading: false,
                error: 'Please enter email and password',
            }));
            return false;
        }

        if (!email || !password) {
            setState((prev) => ({
                ...prev,
                isLoading: false,
                error: 'Please enter email and password',
            }));
            return false;
        }

        try {
            const res = await api.login(email, password);
            if (res.error || !res.data?.access_token) {
                setState((prev) => ({
                    ...prev,
                    isLoading: false,
                    error: res.error?.message || 'Login failed',
                }));
                return false;
            }
            localStorage.setItem(TOKEN_KEY, res.data.access_token);
            setState({
                user: res.data.user,
                isLoading: false,
                isAuthenticated: true,
                error: null,
            });
            return true;
        } catch (err) {
            setState((prev) => ({
                ...prev,
                isLoading: false,
                error: err instanceof Error ? err.message : 'Login failed',
            }));
            return false;
        }
    };

    const logout = async () => {
        try {
            if (!USE_DUMMY_AUTH) await api.logout();
        } catch {
            // Token may already be invalidated server-side; clear locally regardless.
        }
        localStorage.removeItem(TOKEN_KEY);
        setState({
            user: null,
            isLoading: false,
            isAuthenticated: false,
            error: null,
        });
    };

    useEffect(() => {
        void checkAuth();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    return (
        <AuthContext.Provider
            value={{
                ...state,
                login,
                logout,
                checkAuth,
            }}
        >
            {children}
        </AuthContext.Provider>
    );
}

export function useAuth() {
    const context = useContext(AuthContext);
    if (context === undefined) {
        throw new Error('useAuth must be used within an AuthProvider');
    }
    return context;
}
