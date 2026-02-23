import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import type { AdminUser } from './api';

// Dummy admin user for development
const DUMMY_ADMIN_USER: AdminUser = {
    id: 'admin-001',
    email: 'admin@talky.ai',
    name: 'Admin User',
    role: 'super_admin',
};

// Development mode - set to true to bypass real auth
const USE_DUMMY_AUTH = true;

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

export function AuthProvider({ children }: { children: ReactNode }) {
    const [state, setState] = useState<AuthState>({
        user: null,
        isLoading: true,
        isAuthenticated: false,
        error: null,
    });

    const checkAuth = async () => {
        // Use dummy auth for development
        if (USE_DUMMY_AUTH) {
            // Simulate a brief loading state
            await new Promise(resolve => setTimeout(resolve, 300));
            setState({
                user: DUMMY_ADMIN_USER,
                isLoading: false,
                isAuthenticated: true,
                error: null,
            });
            return;
        }

        // Real auth logic (disabled for now)
        const token = localStorage.getItem('admin_token');

        if (!token) {
            setState({
                user: null,
                isLoading: false,
                isAuthenticated: false,
                error: null,
            });
            return;
        }

        // In production, verify token with API
        setState({
            user: null,
            isLoading: false,
            isAuthenticated: false,
            error: null,
        });
    };

    const login = async (email: string, password: string): Promise<boolean> => {
        setState(prev => ({ ...prev, isLoading: true, error: null }));

        // Use dummy auth for development
        if (USE_DUMMY_AUTH) {
            // Simulate API delay
            await new Promise(resolve => setTimeout(resolve, 500));

            // Accept any credentials for development
            if (email && password) {
                setState({
                    user: { ...DUMMY_ADMIN_USER, email },
                    isLoading: false,
                    isAuthenticated: true,
                    error: null,
                });
                return true;
            } else {
                setState(prev => ({
                    ...prev,
                    isLoading: false,
                    error: 'Please enter email and password',
                }));
                return false;
            }
        }

        // Real login logic (disabled for now)
        setState(prev => ({
            ...prev,
            isLoading: false,
            error: 'Real authentication not configured',
        }));
        return false;
    };

    const logout = async () => {
        // Clear auth state
        localStorage.removeItem('admin_token');
        setState({
            user: null,
            isLoading: false,
            isAuthenticated: false,
            error: null,
        });
    };

    useEffect(() => {
        checkAuth();
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
