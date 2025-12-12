"use client";

import React, { createContext, useContext, useState, useEffect, ReactNode } from "react";
import { api, MeResponse, VerifyOtpResponse } from "@/lib/api";

interface AuthContextType {
    user: MeResponse | null;
    loading: boolean;
    login: (email: string) => Promise<string>;
    register: (email: string, businessName: string, name?: string) => Promise<string>;
    verifyOtp: (email: string, token: string) => Promise<VerifyOtpResponse>;
    logout: () => Promise<void>;
    setToken: (token: string) => void;
    refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
    const [user, setUser] = useState<MeResponse | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        checkAuth();
    }, []);

    async function checkAuth() {
        // Check if we have a token stored
        if (typeof window !== "undefined") {
            const token = localStorage.getItem("token");
            if (!token) {
                setLoading(false);
                return;
            }
        }

        try {
            const me = await api.getMe();
            setUser(me);
        } catch {
            // Token invalid or expired
            setUser(null);
            if (typeof window !== "undefined") {
                localStorage.removeItem("token");
            }
        } finally {
            setLoading(false);
        }
    }

    async function refreshUser() {
        try {
            const me = await api.getMe();
            setUser(me);
        } catch {
            setUser(null);
        }
    }

    async function login(email: string): Promise<string> {
        const response = await api.login(email);
        // Return message - actual auth happens via OTP verification
        return typeof response === "string" ? response : response?.message || "Verification code sent!";
    }

    async function register(
        email: string,
        businessName: string,
        name?: string
    ): Promise<string> {
        const response = await api.register(email, businessName, "basic", name);
        // Return message - actual auth happens via OTP verification
        return typeof response === "string" ? response : response?.message || "Verification code sent!";
    }

    async function verifyOtp(email: string, token: string): Promise<VerifyOtpResponse> {
        const response = await api.verifyOtp(email, token);
        // Store token and refresh user
        api.setToken(response.access_token);
        if (typeof window !== "undefined") {
            localStorage.setItem("refresh_token", response.refresh_token);
        }
        await checkAuth();
        return response;
    }

    async function logout() {
        try {
            await api.logout();
        } catch {
            // Ignore errors
        }
        setUser(null);
        if (typeof window !== "undefined") {
            localStorage.removeItem("token");
            localStorage.removeItem("refresh_token");
        }
    }

    function setToken(token: string) {
        api.setToken(token);
        checkAuth();
    }

    return (
        <AuthContext.Provider value={{ user, loading, login, register, verifyOtp, logout, setToken, refreshUser }}>
            {children}
        </AuthContext.Provider>
    );
}

export function useAuth() {
    const context = useContext(AuthContext);
    if (context === undefined) {
        throw new Error("useAuth must be used within an AuthProvider");
    }
    return context;
}
