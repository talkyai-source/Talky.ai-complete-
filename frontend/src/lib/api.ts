const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

export interface AuthResponse {
    id: string;
    email: string;
    business_name?: string;
    role: string;
    minutes_remaining: number;
    message: string;
}

export interface MeResponse {
    id: string;
    email: string;
    name?: string;
    business_name?: string;
    role: string;
    minutes_remaining: number;
}

export interface ApiError {
    detail: string;
}

export interface VerifyOtpResponse {
    access_token: string;
    refresh_token: string;
    user_id: string;
    email: string;
    message: string;
}

class ApiClient {
    private baseUrl: string;
    private token: string | null = null;

    constructor(baseUrl: string) {
        this.baseUrl = baseUrl;
        if (typeof window !== "undefined") {
            this.token = localStorage.getItem("token");
        }
    }

    setToken(token: string) {
        this.token = token;
        if (typeof window !== "undefined") {
            localStorage.setItem("token", token);
        }
    }

    clearToken() {
        this.token = null;
        if (typeof window !== "undefined") {
            localStorage.removeItem("token");
        }
    }

    private async request<T>(
        endpoint: string,
        options: RequestInit = {}
    ): Promise<T> {
        const headers: HeadersInit = {
            "Content-Type": "application/json",
            ...(options.headers || {}),
        };

        if (this.token) {
            (headers as Record<string, string>)["Authorization"] = `Bearer ${this.token}`;
        }

        const response = await fetch(`${this.baseUrl}${endpoint}`, {
            ...options,
            headers,
        });

        if (!response.ok) {
            const error: ApiError = await response.json().catch(() => ({
                detail: "An error occurred",
            }));
            throw new Error(error.detail);
        }

        return response.json();
    }

    // Auth endpoints
    async login(email: string): Promise<AuthResponse> {
        return this.request<AuthResponse>("/auth/login", {
            method: "POST",
            body: JSON.stringify({ email }),
        });
    }

    async verifyOtp(email: string, token: string): Promise<VerifyOtpResponse> {
        return this.request<VerifyOtpResponse>("/auth/verify-otp", {
            method: "POST",
            body: JSON.stringify({ email, token }),
        });
    }

    async register(
        email: string,
        businessName: string,
        planId: string = "basic",
        name?: string
    ): Promise<AuthResponse> {
        return this.request<AuthResponse>("/auth/register", {
            method: "POST",
            body: JSON.stringify({
                email,
                business_name: businessName,
                plan_id: planId,
                name,
            }),
        });
    }

    async getMe(): Promise<MeResponse> {
        return this.request<MeResponse>("/auth/me");
    }

    async logout(): Promise<void> {
        await this.request("/auth/logout", { method: "POST" });
        this.clearToken();
    }

    // Health check
    async health(): Promise<{ status: string }> {
        return this.request("/health");
    }
}

export const api = new ApiClient(API_BASE);
